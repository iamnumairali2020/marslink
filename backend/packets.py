import json
import zlib
import time
from typing import Dict, Any

def compute_crc(packet: Dict[str, Any]) -> int:
    """
    This function finds a CRC32 checksum
    """
    # Exclude 'crc' to avoid circular dependency during calculation
    packet_copy = {k: v for k, v in packet.items() if k != 'crc'}
    
    # sort_keys=True ensures field ordering never changes the checksum.
    # separators removes whitespace padding differences.
    serialized = json.dumps(packet_copy, sort_keys=True, separators=(',', ':'))
    
    # Compute CRC32 and mask with 0xFFFFFFFF to guarantee an unsigned 32-bit int
    return zlib.crc32(serialized.encode('utf-8')) & 0xFFFFFFFF

def verify_crc(packet: Dict[str, Any]) -> bool:
    """
    Validates the packet integrity by comparing its embedded CRC 
    against a freshly computed CRC of its contents.
    """
    if 'crc' not in packet:
        return False
    
    received_crc = packet['crc']
    computed_crc = compute_crc(packet)
    return received_crc == computed_crc

def make_packet(
    mission_id: str,
    crew_id: str,
    seq: int,
    hr: float,
    spo2: float,
    dose_msv: float,
    ts: float = None
) -> Dict[str, Any]:
    """
    Constructs a standardized telemetry packet dictionary and appends its CRC32 checksum.
    """
    if ts is None:
        ts = time.time()
        
    packet = {
        "mission_id": mission_id,
        "crew_id": crew_id,
        "seq": seq,
        "ts": ts,
        "hr": hr,
        "spo2": spo2,
        "dose_msv": dose_msv
    }
    
    # Attach computed checksum
    packet["crc"] = compute_crc(packet)
    return packet