# Adaptive QoS Engine

A local network operations dashboard for a deterministic home broadband QoS simulator. Simulated rates and measurements come from backend traffic demand, scheduling, congestion, queue, and delay calculations. The application does not inspect private application payloads. It does not claim that simulated traffic is real interface traffic.

## Run locally

Requires Python 3.11+ and Node.js 20+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn qos.main:app --app-dir backend --reload
```

In a second terminal:

```powershell
npm install
npm run dev
```

Open http://localhost:5173. The Vite server proxies API and WebSocket traffic to FastAPI on port 8000. Experiment history is stored in `backend/qos_history.sqlite3` (override with `QOS_DB_PATH`).

## Verify

```powershell
pip install pytest httpx
$env:PYTHONPATH="backend"
pytest backend\tests -q
npm run build
```

## Application notes

- Dashboard and experiment lab stream backend telemetry over WebSockets and automatically reconnect.
- Four deterministic scenarios are available; baseline and adaptive comparisons use the same seed and offered workload.
- The adaptive scheduler applies minimum guarantees, weighted max-min fill, class caps, and proportional scaling when minimums are infeasible.
- Experiment outputs are saved to SQLite and export as CSV or JSON.
- Linux `tc` support is isolated in an opt-in adapter. It requires a supported Linux host, `tc`, a validated interface name, and explicit confirmation. Simulation mode is the default.
- The competition case-study PDF was not included in the provided attachments; implementation follows the pasted requirements.
