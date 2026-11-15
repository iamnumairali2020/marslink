# ARES-1 MarsLink: Deep-Space Telemetry & Mission Control Simulation

**MarsLink** is an end-to-end mission control web application that simulates the real-world networking challenges of interplanetary communication between Earth and Mars. Built with **FastAPI**, **WebSockets**, **SQLite**, and a vanilla **HTML/CSS/JS** dashboard, it demonstrates how spacecraft telemetry survives massive light-speed delays, data corruption, packet loss, and solar blackouts.

---

## Key Features

* **Real-Time Planetary Physics & Live APIs:**
  * **NASA JPL Horizons Integration:** Fetches the live, real-time astronomical distance (in AU) between Earth and Mars to calculate accurate one-way light-speed delays (~11–15 minutes), compressed via a configurable `60x` demo time-scale factor.
  * **NOAA Space Weather Integration:** Streams live solar proton flux ($\ge 10\text{ MeV}$) from the NOAA Space Weather Prediction Center (SWPC) and triggers automated **S1 Solar Storm Warnings** when radiation thresholds are exceeded.
  * *Resilient Fallback:* Includes automatic caching and graceful fallback defaults if external APIs are offline.
* **Resilient Deep-Space Networking Protocol:**
  * **CRC32 Integrity Verification:** Every telemetry packet is serialized with a CRC32 checksum to catch bit-flips and radiation-induced corruption upon arrival.
  * **Gap Detection & Bidirectional Resends:** When packets are dropped or corrupted, the ground receiver detects missing sequence numbers and dispatches retransmission requests across the round-trip deep-space delay queue.
  * **Blackout Store-and-Forward:** Simulates solar conjunction blackouts where Mars is cut off from Earth. The spacecraft buffers telemetry in a local outbox and flushes all queued records once communication is restored.
* **Database Persistence & Deduplication:**
  * Backed by **SQLite** with `UNIQUE(crew_id, seq)` constraints at the schema level to guarantee duplicate packets are cleanly dropped while preserving full mission history.
* **Interactive Mission Control Dashboard:**
  * **Crew Vitals Cards (C1–C4):** Live Heart Rate, $\text{SpO}_2$, and cumulative radiation dose ($\text{mSv}$) with dynamic `LIVE`, `STALE`, and `NO SIGNAL` freshness indicators.
  * **Link Health Telemetry:** Live counters tracking received, corrupted, missing, recovered, and permanently lost packets.
  * **Live Demo Controls:** Interactive **Deep Space Blackout** toggle and **Fault Corruption Rate** slider (`0%–100%`) to stress-test the protocol live.
  * **Auto-Reconnecting WebSockets:** Automatic 2-second client retry loop with historical state recovery.

---

## System Architecture

```text
marslink/
├── backend/
│   ├── __init__.py
│   ├── main.py          # FastAPI server, WebSocket manager, /control endpoints, lifespan tasks
│   ├── simulator.py     # Spacecraft telemetry generator, fault injector, store-and-forward outbox
│   ├── link.py          # Bidirectional deep-space delay queue & light-speed time compression
│   ├── receiver.py      # 5-step ground pipeline (CRC, dedup, gap detection, alerts, broadcast)
│   ├── packets.py       # Immutable packet serialization & CRC32 checksum verification
│   ├── integrations.py  # Async NASA JPL Horizons & NOAA SWPC API clients with caching
│   ├── db.py            # SQLite schema initialization, UNIQUE constraints, and query helpers
│   └── marslink.db      # Auto-generated SQLite database file
├── frontend/
│   ├── index.html       # Mission control dashboard layout
│   ├── style.css        # Dark space-themed UI styling
│   └── app.js           # WebSocket client, DOM updates, and live demo control handlers
└── README.md
```

---

## Getting Started

### 1. Prerequisites
* Python 3.9 or higher
* `pip` package manager

### 2. Installation
Clone the repository and navigate to the project root directory (the parent folder containing `marslink/`):

```bash
# Create and activate a virtual environment
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

# Install required dependencies (includes WebSocket support)
pip install fastapi "uvicorn[standard]" httpx pydantic
```

### 3. Running the Application
Start the FastAPI backend server with Uvicorn from the project root:

```bash
uvicorn marslink.backend.main:app --reload --host 127.0.0.1 --port 8000
```

Once the server starts, open your browser and navigate to:
**`http://127.0.0.1:8000`**

*(Note: Allow ~10–15 seconds after initial startup for the first simulated telemetry packets to traverse the deep-space light delay queue and appear on the dashboard.)*

---

## Live Demo Walkthrough

1. **Observe Normal Operations:**
   * Verify the top bar shows live AU distance from NASA JPL Horizons and live solar proton flux from NOAA.
   * Confirm all four crew members (`C1`–`C4`) display green **`LIVE`** badges with real-time vitals.
2. **Test Fault Injection & Auto-Recovery:**
   * Drag the **Fault Corruption Rate** slider to `25%–50%`.
   * Watch the **Corrupted (CRC)** and **Missing (Gaps)** counters increment in the Link Health panel, followed by **Recovered (Resend)** climbing as the ground station requests and receives retransmissions across the delay loop.
3. **Test Deep-Space Blackout & Store-and-Forward:**
   * Toggle **Deep Space Blackout** ON.
   * Watch the Link Status switch to **`BLACKOUT`** and crew cards transition from `LIVE` to `STALE` to `NO SIGNAL`.
   * Toggle **Deep Space Blackout** OFF to watch the spacecraft flush its buffered outbox, restoring all telemetry to Earth with zero data loss.

---

## API & WebSocket Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` | `GET` | Serves the Mission Control frontend dashboard. |
| `/ws` | `WS` | Real-time WebSocket stream broadcasting telemetry packets, link stats, and space weather. |
| `/control` | `POST` | Updates live simulation parameters (`blackout`, `corrupt_rate`, `drop_rate`, `time_scale`). |
| `/status` | `GET` | Returns current link metrics, API sources, and receiver sequence state. |
| `/history/{crew_id}` | `GET` | Retrieves recent packet history from SQLite for a specific crew member. |
| `/events` | `GET` | Fetches the most recent mission events (`corrupted`, `missing`, `resent`, `alert`, `blackout`). |