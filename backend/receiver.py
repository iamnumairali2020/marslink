import time
from typing import Dict, Any, List, Optional, Callable
from marslink.backend import packets
from marslink.backend import db

class GroundReceiver:
    def __init__(self, link_instance=None, broadcast_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        """
        Initializes the ground receiver pipeline.
        :param link_instance: Reference to DeepSpaceLink to trigger ground-to-space resend requests.
        :param broadcast_callback: Async or sync callback to push telemetry/events to the FastAPI WebSocket.
        """
        self.link = link_instance
        self.broadcast_callback = broadcast_callback
        
        # State tracking per crew member
        self.highest_seq: Dict[str, int] = {}
        self.seen_seqs: Dict[str, set] = {}  # crew_id -> set of received valid seqs
        
        # Missing tracking: crew_id -> {seq: retry_count}
        self.pending_missing: Dict[str, Dict[int, int]] = {}
        
        # Link health stats
        self.stats = {
            "received": 0,
            "corrupted": 0,
            "missing": 0,
            "recovered": 0,
            "lost": 0
        }

    def set_link(self, link_instance):
        self.link = link_instance

    def set_broadcast_callback(self, callback: Callable[[Dict[str, Any]], None]):
        self.broadcast_callback = callback

    async def process_packet(self, packet: Dict[str, Any]):
        """
        Processes an incoming telemetry packet through the 5-step ground pipeline.
        """
        now = time.time()
        crew_id = packet.get("crew_id")
        seq = packet.get("seq")
        ts = packet.get("ts", now)
        
        if not crew_id or seq is None:
            return

        # Initialize crew state if not present
        if crew_id not in self.highest_seq:
            self.highest_seq[crew_id] = seq - 1
            self.seen_seqs[crew_id] = set()
            self.pending_missing[crew_id] = {}

        # ==========================================
        # STEP 1: CRC CHECK
        # ==========================================
        if not packets.verify_crc(packet):
            self.stats["corrupted"] += 1
            db.insert_event(time.time(), "corrupted", crew_id, f"Packet seq {seq} failed CRC check.")
            
            # Count corrupted sequence as missing/needing resend if valid structure
            if crew_id in self.highest_seq and seq > self.highest_seq[crew_id]:
                self._track_missing(crew_id, seq)
            
            await self._push_update()
            return

        # ==========================================
        # STEP 2: DUPLICATE CHECK
        # ==========================================
        if seq in self.seen_seqs[crew_id]:
            # Duplicate packet (could be a duplicate delivery or successful resend arrival)
            if packet.get("resent", False):
                self.stats["recovered"] += 1
                db.insert_event(time.time(), "resent", crew_id, f"Resent packet seq {seq} successfully recovered.")
                # Clear from missing tracking if it was pending
                if seq in self.pending_missing[crew_id]:
                    del self.pending_missing[crew_id][seq]
            return

        # Mark as seen
        self.seen_seqs[crew_id].add(seq)
        self.stats["received"] += 1

        # If this packet was previously tracked as missing, mark it recovered
        if seq in self.pending_missing[crew_id]:
            self.stats["recovered"] += 1
            db.insert_event(time.time(), "resent", crew_id, f"Missing packet seq {seq} recovered via resend.")
            del self.pending_missing[crew_id][seq]

        # ==========================================
        # STEP 3: GAP CHECK & RESEND REQUESTS
        # ==========================================
        current_highest = self.highest_seq[crew_id]
        if seq > current_highest + 1:
            # We skipped ahead! Mark intermediate sequence numbers as missing
            missing_seqs_to_request = []
            for missing_seq in range(current_highest + 1, seq):
                if missing_seq not in self.seen_seqs[crew_id]:
                    self._track_missing(crew_id, missing_seq)
                    missing_seqs_to_request.append(missing_seq)
            
            if missing_seqs_to_request and self.link:
                db.insert_event(time.time(), "missing", crew_id, f"Gap detected! Missing seqs: {missing_seqs_to_request}. Requesting resend.")
                await self.link.enqueue_ground_to_space(crew_id, missing_seqs_to_request)

        # Update highest sequence encountered
        if seq > current_highest:
            self.highest_seq[crew_id] = seq

        # Check if any other pending missing packets have exceeded 3 retry tries -> Mark LOST
        self._check_lost_packets(crew_id)

        # ==========================================
        # STEP 4: SAVE TO SQLITE & PUSH TO DASHBOARD
        # ==========================================
        received_at = time.time()
        is_resent = 1 if packet.get("resent", False) else 0
        
        db.insert_packet(
            mission_id=packet.get("mission_id", "ARES-1"),
            crew_id=crew_id,
            seq=seq,
            ts=ts,
            received_at=received_at,
            hr=packet.get("hr", 0.0),
            spo2=packet.get("spo2", 0.0),
            dose_msv=packet.get("dose_msv", 0.0),
            resent=is_resent
        )

        # ==========================================
        # STEP 5: ALERTS & THRESHOLD CHECKING
        # ==========================================
        hr = packet.get("hr", 75.0)
        spo2 = packet.get("spo2", 97.0)
        dose = packet.get("dose_msv", 0.0)

        if hr > 120 or hr < 45:
            db.insert_event(received_at, "alert", crew_id, f"CRITICAL HR: {hr} bpm")
        if spo2 < 92:
            db.insert_event(received_at, "alert", crew_id, f"CRITICAL SpO2: {spo2}%")
        if dose > 5.0:
            db.insert_event(received_at, "alert", crew_id, f"HIGH RADIATION DOSE: {dose} mSv")

        await self._push_update()

    def _track_missing(self, crew_id: str, seq: int):
        """Tracks a missing sequence number and initializes or increments its retry count."""
        missing_dict = self.pending_missing[crew_id]
        if seq not in missing_dict:
            missing_dict[seq] = 1
        else:
            missing_dict[seq] += 1
        self.stats["missing"] += 1

    def _check_lost_packets(self, crew_id: str):
        """Gives up on missing packets after 3 retry attempts and marks them lost."""
        missing_dict = self.pending_missing[crew_id]
        to_drop = []
        for seq, retries in missing_dict.items():
            if retries > 3:
                to_drop.append(seq)
                self.stats["lost"] += 1
                db.insert_event(time.time(), "lost", crew_id, f"Given up on seq {seq} after 3 resend retries. Marked LOST.")
        
        for seq in to_drop:
            del missing_dict[seq]

    def compute_freshness(self, packet_ts: float, light_delay: float) -> str:
        """
        Computes data freshness / signal health state:
        - LIVE: age < delay + 5s
        - STALE: age < delay + 30s
        - NO SIGNAL: older than that
        """
        age = time.time() - packet_ts
        if age < (light_delay + 5.0):
            return "LIVE"
        elif age < (light_delay + 30.0):
            return "STALE"
        else:
            return "NO SIGNAL"

    async def _push_update(self):
        """Triggers dashboard WebSocket broadcast if callback is registered."""
        if self.broadcast_callback:
            data = {
                "type": "telemetry_update",
                "stats": self.stats
            }
            if asyncio.iscoroutinefunction(self.broadcast_callback):
                await self.broadcast_callback(data)
            else:
                self.broadcast_callback(data)