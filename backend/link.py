import asyncio
import time
from typing import Dict, Any, List, Callable, Optional

class DeepSpaceLink:
    def __init__(self, real_light_delay: float = 890.0, time_scale: float = 60.0):
        """
        Initializes the deep space communication channel.
        :param real_light_delay: Light travel time in seconds at 1 AU (~890s for Earth-Mars typical).
        :param time_scale: Compression factor for demo playback (e.g., 60.0 means 60x faster).
        """
        self.real_light_delay = real_light_delay
        self.time_scale = time_scale
        
        # Unified delay queue holding items with target delivery timestamps and direction tags
        self.queue: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        
        # Callbacks for both directions of travel
        self.receiver_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self.spacecraft_callback: Optional[Callable[[str, List[int]], None]] = None

    def set_receiver_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers the ground receiver callback for telemetry packets."""
        self.receiver_callback = callback

    def set_spacecraft_callback(self, callback: Callable[[str, List[int]], None]):
        """Registers the spacecraft simulator callback for resend requests."""
        self.spacecraft_callback = callback

    def update_light_delay_from_au(self, au: float):
        """Updates real light delay from Astronomical Units (1 AU ≈ 499 light seconds)."""
        self.real_light_delay = au * 499.0

    def set_time_scale(self, scale: float):
        """Updates the demo time scale factor."""
        if scale > 0:
            self.time_scale = scale

    @property
    def current_delay_seconds(self) -> float:
        """Calculates demo-scaled delay in seconds."""
        if self.time_scale <= 0:
            return self.real_light_delay
        return self.real_light_delay * (1.0 / self.time_scale)

    async def enqueue_space_to_ground(self, packet: Dict[str, Any]):
        """Enqueues a telemetry packet traveling from spacecraft to ground."""
        deliver_at = time.time() + self.current_delay_seconds
        async with self._lock:
            self.queue.append({
                "deliver_at": deliver_at,
                "direction": "to_ground",
                "payload": packet
            })

    async def enqueue_ground_to_space(self, crew_id: str, seqs: List[int]):
        """Enqueues a resend request traveling from ground back to spacecraft (round trip)."""
        deliver_at = time.time() + self.current_delay_seconds
        async with self._lock:
            self.queue.append({
                "deliver_at": deliver_at,
                "direction": "to_space",
                "crew_id": crew_id,
                "seqs": seqs
            })

    async def run_loop(self):
        """Background loop running every ~100ms to dispatch items whose delivery time has passed."""
        while True:
            now = time.time()
            ready_items = []
            
            async with self._lock:
                remaining = []
                for item in self.queue:
                    if now >= item["deliver_at"]:
                        ready_items.append(item)
                    else:
                        remaining.append(item)
                self.queue = remaining
            
            # Dispatch ready items to their respective destinations
            for item in ready_items:
                if item["direction"] == "to_ground" and self.receiver_callback:
                    pkt = item["payload"]
                    if asyncio.iscoroutinefunction(self.receiver_callback):
                        await self.receiver_callback(pkt)
                    else:
                        self.receiver_callback(pkt)
                        
                elif item["direction"] == "to_space" and self.spacecraft_callback:
                    crew_id = item["crew_id"]
                    seqs = item["seqs"]
                    if asyncio.iscoroutinefunction(self.spacecraft_callback):
                        await self.spacecraft_callback(crew_id, seqs)
                    else:
                        self.spacecraft_callback(crew_id, seqs)
            
            await asyncio.sleep(0.1)