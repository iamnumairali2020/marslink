import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import os

from marslink.backend import db
from marslink.backend import packets
from marslink.backend import simulator
from marslink.backend import link
from marslink.backend import receiver
from marslink.backend import integrations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("marslink.main")

# Global instances initialized on startup
db_instance = None
link_instance = None
sim_instance = None
receiver_instance = None

class ConnectionManager:
    """Manages active WebSocket connections for real-time dashboard broadcasting."""
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"WebSocket broadcast error: {e}")
                self.disconnect(connection)

manager = ConnectionManager()

async def receiver_broadcast_handler(data: Dict[str, Any]):
    """Bridge callback from GroundReceiver to WebSocket broadcast manager."""
    # Enrich broadcast payload with latest link stats, AU distance, and NOAA flux status
    au, au_source = integrations.get_current_mars_distance()
    flux_info = integrations.get_proton_flux_status()
    
    enriched_payload = {
        **data,
        "link": {
            "status": "BLACKOUT" if sim_instance.is_blackout else "UP",
            "real_light_delay": link_instance.real_light_delay,
            "demo_delay": link_instance.current_delay_seconds,
            "time_scale": link_instance.time_scale,
            "au": au,
            "au_source": au_source
        },
        "radiation": flux_info
    }
    await manager.broadcast(enriched_payload)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to initialize database and start all background asyncio tasks."""
    global link_instance, sim_instance, receiver_instance
    
    logger.info("Initializing MarsLink backend database...")
    db.init_db()
    
    # Instantiate core components
    link_instance = link.DeepSpaceLink(real_light_delay=890.0, time_scale=60.0)
    sim_instance = simulator.SpacecraftSimulator(mission_id="ARES-1", crew_ids=["C1", "C2", "C3", "C4"])
    receiver_instance = receiver.GroundReceiver(link_instance=link_instance, broadcast_callback=receiver_broadcast_handler)
    
    # Wire callbacks together
    sim_instance.set_link_callback(link_instance.enqueue_space_to_ground)
    link_instance.set_spacecraft_callback(sim_instance.handle_resend_request)
    link_instance.set_receiver_callback(receiver_instance.process_packet)
    
    # Start background async loops
    logger.info("Starting background tasks (Simulator, Link Dispatch, Horizons, NOAA)...")
    sim_task = asyncio.create_task(sim_instance.run_loop())
    link_task = asyncio.create_task(link_instance.run_loop())
    horizons_task = asyncio.create_task(integrations.horizons_background_loop())
    noaa_task = asyncio.create_task(integrations.noaa_background_loop())
    
    yield
    
    logger.info("Shutting down MarsLink backend tasks...")
    sim_task.cancel()
    link_task.cancel()
    horizons_task.cancel()
    noaa_task.cancel()

app = FastAPI(title="MarsLink Mission Control Backend", version="1.0.0", lifespan=lifespan)

# Enable CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic model for control endpoint payloads
class ControlPayload(BaseModel):
    blackout: Optional[bool] = Field(default=None, description="Toggle communication blackout state")
    corrupt_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Fault injection corruption rate (0.0 - 1.0)")
    drop_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Fault injection drop rate (0.0 - 1.0)")
    time_scale: Optional[float] = Field(default=None, gt=0.0, description="Demo playback time compression scale factor")

@app.post("/control")
async def control_simulation(payload: ControlPayload):
    """Control endpoint to modify simulation parameters live during the demo."""
    if payload.blackout is not None:
        await sim_instance.set_blackout(payload.blackout)
        db.insert_event(asyncio.get_event_loop().time(), "blackout", None, f"Blackout state changed to: {payload.blackout}")
        
    if payload.corrupt_rate is not None:
        sim_instance.corrupt_rate = payload.corrupt_rate
        
    if payload.drop_rate is not None:
        sim_instance.drop_rate = payload.drop_rate
        
    if payload.time_scale is not None:
        link_instance.set_time_scale(payload.time_scale)
        
    au, au_source = integrations.get_current_mars_distance()
    # Update link real delay if AU changes or needs syncing
    link_instance.update_light_delay_from_au(au)

    return {
        "status": "success",
        "blackout": sim_instance.is_blackout,
        "corrupt_rate": sim_instance.corrupt_rate,
        "drop_rate": sim_instance.drop_rate,
        "time_scale": link_instance.time_scale,
        "demo_delay_seconds": link_instance.current_delay_seconds
    }

@app.get("/status")
async def get_status():
    """Returns current system health, link parameters, and stats."""
    au, au_source = integrations.get_current_mars_distance()
    flux_info = integrations.get_proton_flux_status()
    return {
        "mission_id": sim_instance.mission_id,
        "blackout": sim_instance.is_blackout,
        "corrupt_rate": sim_instance.corrupt_rate,
        "drop_rate": sim_instance.drop_rate,
        "time_scale": link_instance.time_scale,
        "real_light_delay": link_instance.real_light_delay,
        "demo_delay_seconds": link_instance.current_delay_seconds,
        "au": au,
        "au_source": au_source,
        "radiation": flux_info,
        "stats": receiver_instance.stats,
        "crew_highest_seq": receiver_instance.highest_seq
    }

@app.get("/history/{crew_id}")
async def get_crew_history(crew_id: str, limit: int = 50):
    """Retrieves recent packet history for a specific crew member to recover state after page refresh."""
    packets_list = db.get_recent_packets(crew_id=crew_id, limit=limit)
    events_list = db.get_recent_events(limit=30)
    return {
        "crew_id": crew_id,
        "packets": packets_list,
        "recent_events": events_list
    }

@app.get("/events")
async def get_events(limit: int = 50):
    """Retrieves recent system events for the dashboard event log."""
    return {"events": db.get_recent_events(limit=limit)}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint connecting the frontend dashboard for real-time telemetry streaming."""
    await manager.connect(websocket)
    try:
        # Send initial status snapshot upon connection
        au, au_source = integrations.get_current_mars_distance()
        flux_info = integrations.get_proton_flux_status()
        await websocket.send_json({
            "type": "init",
            "stats": receiver_instance.stats,
            "link": {
                "status": "BLACKOUT" if sim_instance.is_blackout else "UP",
                "real_light_delay": link_instance.real_light_delay,
                "demo_delay": link_instance.current_delay_seconds,
                "time_scale": link_instance.time_scale,
                "au": au,
                "au_source": au_source
            },
            "radiation": flux_info,
            "recent_events": db.get_recent_events(limit=20)
        })
        
        while True:
            # Keep connection alive and listen for any client messages if needed
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# Optional: Serve frontend static files if frontend folder is present
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")