// NAMNAM-1 MarsLink Dashboard Client Logic

const CREW_IDS = ["C1", "C2", "C3", "C4"];
const latestCrewData = {};
const SIGNAL_STALE_SECONDS = 30;

// DOM Elements
const connectionBadge = document.getElementById("connection-badge");
const topAu = document.getElementById("top-au");
const topAuSource = document.getElementById("top-au-source");
const topRealDelay = document.getElementById("top-real-delay");
const topDemoDelay = document.getElementById("top-demo-delay");
const topLinkStatus = document.getElementById("top-link-status");

const crewCardsContainer = document.getElementById("crew-cards-container");
const eventLog = document.getElementById("event-log");

const statReceived = document.getElementById("stat-received");
const statCorrupted = document.getElementById("stat-corrupted");
const statMissing = document.getElementById("stat-missing");
const statRecovered = document.getElementById("stat-recovered");
const statLost = document.getElementById("stat-lost");

const fluxValue = document.getElementById("flux-value");
const fluxSource = document.getElementById("flux-source");
const stormWarning = document.getElementById("storm-warning");

const blackoutToggle = document.getElementById("blackout-toggle");
const corruptionSlider = document.getElementById("corruption-slider");
const corruptionVal = document.getElementById("corruption-val");

// Initialize Crew Cards UI
function initCrewCards() {
    crewCardsContainer.innerHTML = "";
    CREW_IDS.forEach(crewId => {
        const card = document.createElement("div");
        card.className = "crew-card nosignal";
        card.id = `crew-card-${crewId}`;
        card.innerHTML = `
            <div class="crew-header">
                <span class="crew-name">Crew ${crewId}</span>
                <span class="signal-tag" id="signal-${crewId}">NO SIGNAL</span>
            </div>
            <div class="crew-metrics">
                <div class="crew-metric-row"><span>Heart Rate:</span> <span id="hr-${crewId}">--</span> bpm</div>
                <div class="crew-metric-row"><span>SpO2:</span> <span id="spo2-${crewId}">--</span>%</div>
                <div class="crew-metric-row"><span>Radiation:</span> <span id="dose-${crewId}">--</span> mSv</div>
            </div>
            <div class="crew-age" id="age-${crewId}">No data received</div>
        `;
        crewCardsContainer.appendChild(card);
    });
}

// WebSocket Management with Auto-Reconnect (every 2 seconds)
let ws = null;
let currentDemoDelay = 15.0; // fallback

function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    connectionBadge.textContent = "CONNECTING...";
    connectionBadge.className = "badge connecting";

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        connectionBadge.textContent = "CONNECTED";
        connectionBadge.className = "badge connected";
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleMessage(data);
        } catch (e) {
            console.error("Failed to parse WebSocket message:", e);
        }
    };

    ws.onclose = () => {
        connectionBadge.textContent = "DISCONNECTED (RETRYING...)";
        connectionBadge.className = "badge disconnected";
        setTimeout(connectWebSocket, 2000); // Retry every 2 seconds
    };

    ws.onerror = (err) => {
        console.error("WebSocket error:", err);
        ws.close();
    };
}

function handleMessage(data) {
    if (data.type === "init") {
        if (data.recent_events) {
            data.recent_events.reverse().forEach(ev => appendEvent(ev));
        }
    }

    if (data.stats) {
        statReceived.textContent = data.stats.received || 0;
        statCorrupted.textContent = data.stats.corrupted || 0;
        statMissing.textContent = data.stats.missing || 0;
        statRecovered.textContent = data.stats.recovered || 0;
        statLost.textContent = data.stats.lost || 0;
    }

    // <--- ADD THIS BLOCK TO UPDATE CREW CARDS WHEN A PACKET ARRIVES --->
    if (data.packet) {
        const pkt = data.packet;
        const crewId = pkt.crew_id;
        const hr = document.getElementById(`hr-${crewId}`);
        const spo2 = document.getElementById(`spo2-${crewId}`);
        const dose = document.getElementById(`dose-${crewId}`);
        const card = document.getElementById(`crew-card-${crewId}`);
        const signalTag = document.getElementById(`signal-${crewId}`);
        const ageTag = document.getElementById(`age-${crewId}`);

        if (hr && spo2 && dose && card && signalTag && ageTag) {
            hr.textContent = pkt.hr;
            spo2.textContent = pkt.spo2;
            dose.textContent = pkt.dose_msv;
            latestCrewData[crewId] = pkt;
            card.className = "crew-card live";
            signalTag.textContent = "LIVE";

            const age = Math.max(0, (Date.now() / 1000) - pkt.ts).toFixed(1);
            ageTag.textContent = `Data as of ${age}s ago`;
        }
    }

    if (data.type === "init") {
        // Recover each crew member's latest vitals when the dashboard reconnects
        // or is opened after telemetry has already started.
        CREW_IDS.forEach(crewId => {
            fetch(`/history/${crewId}?limit=1`)
                .then(response => response.ok ? response.json() : Promise.reject(response.status))
                .then(history => {
                    const pkt = history.packets && history.packets[0];
                    if (pkt && !latestCrewData[crewId]) {
                        handleMessage({ packet: pkt });
                    }
                })
                .catch(error => console.error(`Failed to load ${crewId} telemetry history:`, error));
        });
    }

    if (data.link) {
        topAu.textContent = `${data.link.au.toFixed(3)} AU`;
        topAuSource.textContent = `(${data.link.au_source})`;
        topRealDelay.textContent = `${Math.round(data.link.real_light_delay)} s`;
        currentDemoDelay = data.link.demo_delay;
        topDemoDelay.textContent = `${currentDemoDelay.toFixed(1)} s`;

        if (data.link.status === "BLACKOUT") {
            topLinkStatus.textContent = "BLACKOUT";
            topLinkStatus.className = "value status-down";
            blackoutToggle.checked = true;
        } else {
            topLinkStatus.textContent = "UP";
            topLinkStatus.className = "value status-up";
            blackoutToggle.checked = false;
        }
    }

    if (data.radiation) {
        fluxValue.textContent = `${data.radiation.flux.toFixed(2)} pfu`;
        fluxSource.textContent = `(${data.radiation.source})`;
        if (data.radiation.s1_storm_warning) {
            stormWarning.classList.remove("hidden");
        } else {
            stormWarning.classList.add("hidden");
        }
    }
}

// Periodic ticker to recalculate data age and signal health on crew cards
setInterval(() => {
    const now = Date.now() / 1000;
    CREW_IDS.forEach(crewId => {
        const pkt = latestCrewData[crewId];
        if (!pkt) return;

        const age = Math.max(0, now - pkt.ts);
        const card = document.getElementById(`crew-card-${crewId}`);
        const signalTag = document.getElementById(`signal-${crewId}`);
        const ageTag = document.getElementById(`age-${crewId}`);
        if (!card || !signalTag || !ageTag) return;

        ageTag.textContent = `Data as of ${age.toFixed(1)}s ago`;
        const stale = age > SIGNAL_STALE_SECONDS;
        card.className = `crew-card ${stale ? "nosignal" : "live"}`;
        signalTag.textContent = stale ? "NO SIGNAL" : "LIVE";
    });
}, 1000);

// Add event to scrolling event log
function appendEvent(ev) {
    const div = document.createElement("div");
    div.className = `event-item ${ev.type}`;
    const timeStr = new Date(ev.time * 1000).toLocaleTimeString();
    div.textContent = `[${timeStr}] [${ev.type.toUpperCase()}] ${ev.crew_id ? ev.crew_id + ': ' : ''}${ev.detail}`;
    eventLog.prepend(div);
    // Limit log size
    if (eventLog.children.length > 100) {
        eventLog.removeChild(eventLog.lastChild);
    }
}

// Control Endpoints Integration
blackoutToggle.addEventListener("change", async () => {
    try {
        await fetch("/control", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ blackout: blackoutToggle.checked })
        });
    } catch (e) {
        console.error("Failed to update blackout control:", e);
    }
});

corruptionSlider.addEventListener("input", () => {
    corruptionVal.textContent = corruptionSlider.value;
});

corruptionSlider.addEventListener("change", async () => {
    const rate = parseFloat(corruptionSlider.value) / 100.0;
    try {
        await fetch("/control", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ corrupt_rate: rate })
        });
    } catch (e) {
        console.error("Failed to update corruption rate:", e);
    }
});

// Initial startup
initCrewCards();
connectWebSocket();