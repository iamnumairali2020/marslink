import json
import zlib
import time
from typing import Dict, Any

def compute_crc(packet: Dict[str, Any]) -> int:
    """
    This function finds a CRC32 (cyclic redundancy check) checksum. 
    It calls the packet, and removes the crc to avoid circular dependency
    Converts the python dictionary to json and sorts it
    Using the zlib, it calls the built in crc32 function, that calculates the checksum.
    """
    
    # ``resent`` is transport metadata added after the original packet is
    # created; it must not invalidate the packet's payload checksum.
    packet_copy = {k: v for k, v in packet.items() if k not in {'crc', 'resent'}}
    
    
    
    serialized = json.dumps(packet_copy, sort_keys=True, separators=(',', ':'))
    
    
    return zlib.crc32(serialized.encode('utf-8')) & 0xFFFFFFFF

def verify_crc(packet: Dict[str, Any]) -> bool:
    """
    Checks if a data packet has been corrupted/changed in any way
    Verifies the checksum, whereas the previous function solves the checksum
    Compares the received CRC with the computed CRC
    If nothing to compare to, it automatically returns false
    Calls the recieved CRC and the solved CRC
    Returns True if they equal each other
    
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
    Makes a standard telemetry packet containing all the parameters, like mission ID
    If a timestamp isn't given, it makes one automatically using the time.time() method which returns the elapsed time in seconds since Unix Epoch (Jan 1, 1970)
    It then calls the compute_crc function, and attaches the crc value to the created dictionary
    Finally, it returns the completed telemetry packet
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