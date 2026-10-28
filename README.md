# Adaptive QoS Engine

A local dashboard and reproducible traffic model for mixed residential broadband traffic. The traffic model uses generated flow metadata only: it does not decrypt or inspect application payloads. Modeled measurements are labeled as synthetic and do not represent live WAN measurements.

The API starts a seeded household traffic scenario automatically, then streams changing traffic model metrics over WebSockets once per second. The **Connected devices** page now opens in **This PC's Wi-Fi** mode: on Windows, the local API can probe the private IPv4 subnet of its active physical Wi-Fi adapter and display real IP/MAC neighbors from the Windows neighbor table. This is a best-effort LAN scan; client isolation and sleeping devices can hide devices, and the router's client list is authoritative. The scan does not provide per-device throughput, hostnames, Wi-Fi bands, or signal strength. Those require router-specific telemetry. A public hosted backend cannot scan a visitor's home Wi-Fi; run a local agent on that network to collect real local data.

## Architecture

```mermaid
flowchart LR
  D[Home devices\nGenerated flow metadata] --> E[Edge gateway model\nMetadata classifier + capacity probes]
  E --> N[Network model\nShared FIFO baseline / per-class queues]
  N --> A[Analytics\nLatency, loss, fairness, CPU, accuracy]
  A --> U[Dashboard\nReact + live WebSocket]
  E --> P[Policy engine\nMinima, weights, caps, intents]
  P --> N
  A --> R[SQLite experiments\nCSV + JSON evidence]
  T[Optional Linux tc adapter] -. explicit opt-in .-> E
```

The adapter is a separate opt-in integration. It requires Linux, `tc`, explicit confirmation, and an interface without a configured root qdisc. It installs a reversible HTB tree with `fq_codel` leaves. Only explicitly DSCP-marked packets match a service class; unmarked traffic goes to Unknown. Real `tc` changes were not run or validated on your computer while building this project.

## Reproducible setup (Windows PowerShell)

Tested with Python 3.11.6, Node.js 24.11.1, npm 11.6.2. Node dependencies are locked in `package-lock.json`; backend direct dependencies are pinned in `backend/requirements.txt`.

From the project folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
npm ci
```

Start the backend in terminal 1:

```powershell
cd C:\Users\HP\Desktop\elx
.\.venv\Scripts\Activate.ps1
python -m uvicorn qos.main:app --app-dir backend --host 127.0.0.1 --port 8001
```

The Vite proxy defaults to backend port 8001, which avoids the reserved port 8000 on this machine. If you choose another backend port, set the same value in terminal 2 before starting Vite, for example `$env:VITE_BACKEND_PORT = "8000"`.

If Windows reports `[WinError 10013]` on port 8001, find a free port:

```powershell
$listener = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) { Get-Process -Id $listener.OwningProcess }
```

Stop that process only if you recognize it. Otherwise choose another available port and set `$env:VITE_BACKEND_PORT` to match; the HTTP and WebSocket proxies both use that setting.

Start the frontend in terminal 2 (leave backend running):

```powershell
cd C:\Users\HP\Desktop\elx
npm run dev
```

Open the local URL printed by Vite, normally `http://localhost:5173`. Check that `/api/health` returns `{"status":"ok", ...}` at `http://127.0.0.1:8001/api/health` (use the port you selected), then use **Experiment Lab → Run comparison**. The live dashboard should show `MODELED DATA`; results are stored in `backend/qos_history.sqlite3` by default. Set `$env:QOS_DB_PATH` before launching the backend to use another SQLite file.

## Project checks

From the repository root, install the pinned development tools, run the existing backend suite, and compile the frontend:

```powershell
python -m pip install -r backend\requirements-dev.txt
$env:PYTHONPATH = "backend"
python -m pytest backend\tests -q
npm run build
```

## Run a reproducible traffic scenario and save evidence

This command runs both policies against the same generated traffic, writes a full report and a row-per-tick CSV, and does not need the web server:

```powershell
cd C:\Users\HP\Desktop\elx\backend
..\.venv\Scripts\Activate.ps1
python -m qos.demo --scenario capacity_drop --seed 42 --ticks 40 --out ..\reports
```

Select `work_call_iso`, `capacity_drop`, `gaming_streams`, or `bulk_backup`. The JSON includes generated inputs, probe samples, classifier decisions, model settings, summaries, and raw time series. The generated `reports` directory is an output location; existing reports are not overwritten when a filename already exists.

## Packet-level Linux lab (real packets, emulated link)

For an experiment using actual Linux kernel packets and `tc` counters, run `scripts/packet_lab.sh` on a Linux machine or VM with root `CAP_NET_ADMIN`, `iproute2`, `iperf3`, and `iputils-ping`:

```bash
sudo apt-get update
sudo apt-get install -y iproute2 iperf3 iputils-ping
sudo bash scripts/packet_lab.sh reports
```

The script creates two private network namespaces joined by a veth cable; traffic never leaves that virtual link. It sends real TCP bulk transfers alongside DSCP-marked UDP media and pings over IPv4 and IPv6 through a 20 Mbps bottleneck. It compares a shared TBF queue with HTB class guarantees and `fq_codel`, prints ping latency/loss, TCP throughput, UDP jitter/loss, and kernel qdisc counters, then removes only its namespaces. A timestamped terminal log is saved in `reports`. This is packet-level network emulation and should be reported as such, not as a measurement of your ISP or home gateway. Use a VM with network-administration capabilities if WSL blocks namespace setup. The app's web dashboard does not yet ingest this script's measurements automatically; the lab log is currently separate evidence.

## What the traffic model measures

- **Classifier:** deterministic metadata rules use transport protocol, DSCP, destination port, packet size, packet rate, burstiness, duration, and observed flow rate. No payload is available to the classifier. Experiment accuracy is computed against generated labels and must not be interpreted as measured accuracy on real households.
- **Capacity:** noisy samples are processed by a median/EWMA estimator with fast downward response and slower upward response. The synthetic probe source observes the configured link capacity with bounded noise; a deployment would need a real probe or interface counter source.
- **Queue and QoE model:** one-second fluid arrivals, finite byte buffers, packet-size-derived queue counts, tail-drop loss, and propagation plus queueing delay. The baseline shares a FIFO queue and divides service in proportion to offered load. The adaptive arm uses separate class queues and the weighted policy. Jitter is the tick-to-tick change in modeled queueing delay; this is not packet-level jitter. Throughput and loss are modeled, not captured from a router.
- **Adaptation:** seconds to the first capacity estimate within 10% of the new configured link rate. A result of `N/A` means the experiment had no capacity step or did not settle during its run.
- **Fairness:** Jain index over active generated flows' served rates. **CPU/tick** is the local traffic model process CPU time, not gateway hardware cost. **Allocation churn** is the average normalized change in per-class scheduled rates between ticks.
- **Comparison:** paired generated traffic, random seed, and initial capacity; baseline and adaptive policies differ. Reports aggregate p95 from all raw tick latencies instead of averaging rolling p95 values.

Scenario loads and impairments are defined in `backend/qos/traffic.py` and included in each report. A scenario is reproducible for a fixed seed and tick count; CPU time varies with host load. The fluid approximation does not model TCP congestion control, real packet timing, Wi-Fi contention, NAT, ISP shaping, flow encryption, multi-hop paths, or hardware offload.

## License notices

Direct third-party package names and license identifiers are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Transitive JavaScript versions are recorded in `package-lock.json`; Python direct runtime and development dependencies are pinned separately.

## Known limitations and next validation steps

This project is a local competition prototype, not a deployed router control plane. Local Wi-Fi discovery reports real IP/MAC neighbors; per-device rates require router-specific traffic counters. The Linux adapter only supports a dedicated interface without an existing root qdisc and DSCP-marked packets; it does not identify apps on its own. The packet lab creates actual packets in isolated namespaces, but must be run on a Linux host with network-administration capabilities and its output is not yet imported into the dashboard. Next evidence needed for production claims: run and retain packet-lab reports, connect a local gateway agent for real device discovery and counters, collect IPv4/IPv6 flow metadata without payload decryption, use larger independently labeled classifier datasets, repeat experiments with confidence intervals, measure gateway CPU, and assess fairness/starvation and controller stability. Keep policy bounds and rollback enabled for any later real-interface trial.
