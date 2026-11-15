import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Tuple, Optional
import httpx

logger = logging.getLogger("marslink.integrations")

# Module-level state cache & sources
_cached_au: float = 1.5          # Default fallback ~1.5 AU (~748.5 light seconds)
_au_source: str = "default"
_cached_flux: float = 1.0        # Default normal proton flux (pfu)
_flux_source: str = "default"

# HTTP client configuration
HTTP_TIMEOUT = 15.0

async def fetch_jpl_horizons() -> Tuple[float, str]:
    """
    Fetches Earth-to-Mars distance (delta) in Astronomical Units (AU) from JPL Horizons API.
    Parses plain text ephemeris data between $$SOE and $$EOE markers.
    """
    global _cached_au, _au_source
    url = "https://ssd.jpl.nasa.gov/api/horizons.api"
    
    # Define time window for ephemeris (today to 2 days out)
    now_utc = datetime.now(timezone.utc)
    start_time = now_utc.strftime("%Y-%m-%d")
    stop_time = (now_utc + timedelta(days=2)).strftime("%Y-%m-%d")
    
    params = {
        "format": "json",
        "COMMAND": "'499'",         # Mars
        "CENTER": "'500@399'",      # Earth geocenter
        "EPHEM_TYPE": "'OBSERVER'",
        "QUANTITIES": "'20'",       # Range and range-rate
        "START_TIME": f"'{start_time}'",
        "STOP_TIME": f"'{stop_time}'",
        "STEP_SIZE": "'1d'"
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            result_text = data.get("result", "")
            if "$$SOE" in result_text and "$$EOE" in result_text:
                soe_idx = result_text.find("$$SOE")
                eoe_idx = result_text.find("$$EOE")
                table_block = result_text[soe_idx:eoe_idx]
                
                # Look for data lines containing 'delta' or numerical range values in AU
                # Horizons quantity 20 output columns typically include calendar date, target name, RA, DEC, and DELTA (AU)
                lines = table_block.strip().split("\n")
                for line in lines:
                    # Look for float-like tokens in the line; delta is typically the second or third floating point column
                    parts = line.strip().split()
                    for part in parts:
                        try:
                            val = float(part)
                            # Earth-Mars distance in AU is typically between 0.35 and 2.67 AU
                            if 0.3 <= val <= 3.0:
                                _cached_au = val
                                _au_source = "live"
                                logger.info(f"Successfully fetched live Mars distance: {_cached_au} AU")
                                return _cached_au, _au_source
                        except ValueError:
                            continue
            
            logger.warning("JPL Horizons response missing expected data tokens. Falling back to cache.")
            if _au_source == "live":
                _au_source = "cached"
            return _cached_au, _au_source

    except Exception as e:
        logger.error(f"Failed to fetch JPL Horizons data: {e}. Falling back to cache/default.")
        if _au_source == "live":
            _au_source = "cached"
        return _cached_au, _au_source

async def fetch_noaa_proton_flux() -> Tuple[float, str]:
    """
    Fetches integral proton flux from NOAA SWPC.
    Filters for energy >= 10 MeV and returns the newest valid flux reading.
    """
    global _cached_flux, _flux_source
    url = "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-1-day.json"

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            readings = response.json()
            
            if isinstance(readings, list) and len(readings) > 0:
                # Filter for >= 10 MeV energy band
                valid_readings = [r for r in readings if r.get("energy") == ">=10 MeV" and r.get("flux") is not None]
                if valid_readings:
                    # Take the latest reading based on time_tag or list order
                    latest = valid_readings[-1]
                    flux_val = float(latest["flux"])
                    _cached_flux = flux_val
                    _flux_source = "live"
                    return _cached_flux, _flux_source
            
            logger.warning("NOAA proton flux response empty or invalid format. Falling back to cache.")
            if _flux_source == "live":
                _flux_source = "cached"
            return _cached_flux, _flux_source

    except Exception as e:
        logger.error(f"Failed to fetch NOAA proton flux: {e}. Falling back to cache/default.")
        if _flux_source == "live":
            _flux_source = "cached"
        return _cached_flux, _flux_source

def get_current_mars_distance() -> Tuple[float, str]:
    """Synchronous getter returning current cached AU and source ('live', 'cached', 'default')."""
    return _cached_au, _au_source

def get_current_proton_flux() -> Optional[float]:
    """Synchronous helper for simulator radiation scaling."""
    return _cached_flux

def get_proton_flux_status() -> Dict[str, Any]:
    """Returns flux metrics and whether S1 solar radiation storm threshold (>= 10 pfu) is met."""
    flux = _cached_flux
    is_storm = flux >= 10.0
    return {
        "flux": flux,
        "source": _flux_source,
        "s1_storm_warning": is_storm
    }

async def horizons_background_loop():
    """Background task: fetches JPL Horizons data every 10 minutes."""
    while True:
        await fetch_jpl_horizons()
        await asyncio.sleep(600)  # 10 minutes

async def noaa_background_loop():
    """Background task: fetches NOAA proton flux every 60 seconds."""
    while True:
        await fetch_noaa_proton_flux()
        await asyncio.sleep(60)   # 60 seconds