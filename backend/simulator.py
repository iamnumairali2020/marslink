import asyncio
import random
import time
from typing import Dict, Any, List, Optional, Callable
from marslink.backend import packets

class SpacecraftSimulator:
    def __init__(self, mission_id: str = "NAMNAM-1", crew_ids: List[str] = None):
        self.mission_id = mission_id
        self.crew_ids = crew_ids or ["C1", "C2", "C3", "C4"]
        
        # Sequence numbers per crew member starting at 1000
        self.seqs = {crew_id: 1000 for crew_id in self.crew_ids}
        
        # Baseline vitals state per crew member
        self.vitals_state = {
            crew_id: {"hr": 75.0, "spo2": 97.0, "dose_msv": 0.1}
            for crew_id in self.crew_ids
        }
        
        # Resend buffer: crew_id -> dict mapping seq -> packet (bounded to last ~200)
        self.resend_buffers: Dict[str, Dict[int, Dict[str, Any]]] = {
            crew_id: {} for crew_id in self.crew_ids
        }
        self.buffer_max_size = 200
        
        # Store-and-forward outbox list for blackout periods
        self.outbox: List[Dict[str, Any]] = []
        
        # Fault injection settings (adjustable via control endpoints)
        self.corrupt_rate = 0.05  # 5% chance of post-CRC corruption
        self.drop_rate = 0.03    # 3% chance packet is never sent
        
        # Link blackout state flag
        self.is_blackout = False
        
        # Callback function to push packets into the link queue
        self.link_enqueue_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def set_link_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Registers the callback function to hand off packets to link.py"""
        self.link_enqueue_callback = callback

    def _get_noaa_flux_multiplier(self) -> float:
        """Integration bonus: scales radiation dose accumulation during high proton flux."""
        try:
            from marslink.backend import integrations
            flux = integrations.get_current_proton_flux()
            if flux is not None and flux >= 10.0:
                return 3.0  # Accelerated dose rate during solar storm
        except Exception:
            pass
        return 1.0

    def generate_packet(self, crew_id: str) -> Dict[str, Any]:
        """Generates the next telemetry packet for a specific crew member with small random noise."""
        state = self.vitals_state[crew_id]
        
        # Add random noise and gently mean-revert towards baseline
        state["hr"] = max(40.0, min(150.0, state["hr"] + random.uniform(-2.0, 2.0)))
        state["hr"] += (75.0 - state["hr"]) * 0.05
        
        state["spo2"] = max(80.0, min(100.0, state["spo2"] + random.uniform(-0.5, 0.5)))
        state["spo2"] += (97.0 - state["spo2"]) * 0.1
        
        # Dose climbs slowly, accelerated if NOAA proton flux is elevated
        flux_mult = self._get_noaa_flux_multiplier()
        state["dose_msv"] += 0.001 * flux_mult

        seq = self.seqs[crew_id]
        self.seqs[crew_id] += 1

        # Construct valid packet with correct CRC via packets.py
        packet = packets.make_packet(
            mission_id=self.mission_id,
            crew_id=crew_id,
            seq=seq,
            hr=round(state["hr"], 1),
            spo2=round(state["spo2"], 1),
            dose_msv=round(state["dose_msv"], 4)
        )

        # Fault Injection: Corruption (mutate a value AFTER CRC is computed so checksum fails)
        if random.random() < self.corrupt_rate:
            target_field = random.choice(["hr", "spo2", "dose_msv"])
            if target_field == "spo2":
                packet[target_field] = round(packet[target_field] + random.uniform(5.0, 15.0), 1)
            elif target_field == "hr":
                packet[target_field] = round(packet[target_field] + random.uniform(30.0, 60.0), 1)
            else:
                packet[target_field] = round(packet[target_field] + 5.0, 4)

        return packet

    def handle_resend_request(self, crew_id: str, requested_seqs: List[int]) -> List[Dict[str, Any]]:
        """Retrieves missing packets from the resend buffer when requested from ground."""
        resent_packets = []
        buf = self.resend_buffers.get(crew_id, {})
        for seq in requested_seqs:
            if seq in buf:
                pkt = buf[seq].copy()
                # Mark as resent for tracking/events
                pkt["resent"] = True
                resent_packets.append(pkt)
        return resent_packets

    def send_to_link(self, packet: Dict[str, Any]):
        """Handles drop rates, resend buffers, and store-and-forward during blackouts."""
        # Check drop rate fault injection
        if random.random() < self.drop_rate:
            return  # Packet dropped entirely

        crew_id = packet["crew_id"]
        seq = packet["seq"]
        
        # Save to resend buffer (keep last ~200 per crew)
        buf = self.resend_buffers[crew_id]
        buf[seq] = packet
        if len(buf) > self.buffer_max_size:
            oldest_seq = min(buf.keys())
            del buf[oldest_seq]

        if self.is_blackout:
            # Store and forward: queue in outbox during blackout
            self.outbox.append(packet)
        else:
            # If coming out of blackout with pending outbox items, flush them first in order
            if self.outbox and self.link_enqueue_callback:
                flush_batch = self.outbox.copy()
                self.outbox.clear()
                for p in flush_batch:
                    self.link_enqueue_callback(p)
            
            # Send current packet to link queue
            if self.link_enqueue_callback:
                self.link_enqueue_callback(packet)

    def set_blackout(self, blackout: bool):
        """Toggles the blackout state and flushes the outbox when connectivity is restored."""
        was_blackout = self.is_blackout
        self.is_blackout = blackout
        if was_blackout and not blackout and self.outbox and self.link_enqueue_callback:
            flush_batch = self.outbox.copy()
            self.outbox.clear()
            for p in flush_batch:
                self.link_enqueue_callback(p)

    async def run_loop(self):
        """Main spacecraft simulation loop running every 1 second (demo time)."""
        while True:
            for crew_id in self.crew_ids:
                packet = self.generate_packet(crew_id)
                self.send_to_link(packet)
            await asyncio.sleep(1.0)