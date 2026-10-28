from __future__ import annotations
import asyncio, csv, io, json, os, sqlite3, time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from .engine import Simulator, SCENARIOS, CLASSES, DEFAULT_POLICIES
from .classifier import FlowMetadata, classify_flow
from .experiments import run_comparison
from .enforcement import LinuxTcAdapter
from .lan_discovery import scan_local_wifi, wifi_adapter_traffic

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("QOS_DB_PATH", ROOT / "qos_history.sqlite3"))
sim = Simulator()
tc = LinuxTcAdapter()
clients: set[WebSocket] = set()

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS experiments (id INTEGER PRIMARY KEY, created_at REAL, scenario TEXT, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS policies (id INTEGER PRIMARY KEY, created_at REAL, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS policy_events (id INTEGER PRIMARY KEY, created_at REAL, payload TEXT NOT NULL)")
    conn.commit()
    return conn

class StartRequest(BaseModel):
    scenario: str = "work_call_iso"
    adaptive: bool = True
    seed: int = 42
class CapacityRequest(BaseModel):
    capacity: float = Field(ge=1, le=1000)
class IntentRequest(BaseModel):
    name: str
    duration: int = Field(ge=1, le=240)
class PolicyRequest(BaseModel):
    policies: dict
class CorrectionRequest(BaseModel):
    session_id: str
    traffic_class: str
class ClassificationRequest(BaseModel):
    metadata: dict
class ExperimentRequest(BaseModel):
    scenario: str
    seed: int = 42
    ticks: int = Field(default=40, ge=10, le=300)
class EnforcementRequest(BaseModel):
    interface: str
    confirmed: bool = False
    capacity_mbps: float = Field(default=100, ge=1, le=1000)

@asynccontextmanager
async def lifespan(app: FastAPI):
    conn=db()
    events=[json.loads(row[0]) for row in conn.execute("SELECT payload FROM policy_events ORDER BY id").fetchall()]
    for event in events:
        sim.policies=event.get("after",sim.policies)
        sim.policy_log.append(event)
        if event.get("label")=="Policy applied": sim.history.append(event)
        elif event.get("label")=="Policy rollback" and sim.history: sim.history.pop()
    conn.close()
    sim.start("work_call_iso", adaptive=True, seed=42)
    task = asyncio.create_task(telemetry_loop())
    yield
    task.cancel()
app = FastAPI(title="Adaptive QoS Engine", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

async def broadcast():
    if not clients: return
    message = {"type": "telemetry", "data": sim.snapshot()}
    for ws in list(clients):
        try: await ws.send_json(message)
        except Exception: clients.discard(ws)

async def telemetry_loop():
    while True:
        await asyncio.sleep(1)
        if sim.running and not sim.paused:
            sim.step()
        await broadcast()

@app.get("/api/health")
def health(): return {"status":"ok", "mode":"simulation", "simulated":True, "database":"sqlite", "enforcement":tc.status()}
@app.get("/api/state")
def state(): return sim.snapshot()
@app.post("/api/network/discovery")
def network_discovery(request: Request):
    # Never let a hosted dashboard use its server as a scanner for a visitor's LAN.
    client = request.client.host if request.client else ""
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    if client not in {"127.0.0.1", "::1", "localhost"} or host not in {"127.0.0.1", "localhost"}:
        raise HTTPException(403, "Local Wi-Fi discovery is available only from the local app on the same computer as the Wi-Fi connection.")
    try:
        return scan_local_wifi()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
@app.get("/api/network/traffic")
def network_traffic(request: Request):
    client = request.client.host if request.client else ""
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    if client not in {"127.0.0.1", "::1", "localhost"} or host not in {"127.0.0.1", "localhost"}:
        raise HTTPException(403, "Local Wi-Fi traffic counters are available only from the local app.")
    try:
        return wifi_adapter_traffic()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
@app.get("/api/scenarios")
def scenarios(): return [{"id":k,"name":v} for k,v in SCENARIOS.items()]
@app.post("/api/simulation/start")
async def start_sim(req: StartRequest):
    try: result=sim.start(req.scenario, req.adaptive, req.seed)
    except ValueError as e: raise HTTPException(400,str(e))
    await broadcast(); return result
@app.post("/api/simulation/pause")
async def pause():
    sim.paused=True; await broadcast(); return sim.snapshot()
@app.post("/api/simulation/resume")
async def resume():
    if not sim.running: raise HTTPException(409,"Start an experiment before resuming")
    sim.paused=False; await broadcast(); return sim.snapshot()
@app.post("/api/simulation/stop")
async def stop():
    sim.running=False; sim.paused=False; await broadcast(); return sim.snapshot()
@app.post("/api/simulation/reset")
async def reset():
    sim.intents=[]
    sim.policies={key:value.copy() for key,value in DEFAULT_POLICIES.items()}
    sim.start("work_call_iso", adaptive=True, seed=42)
    await broadcast(); return sim.snapshot()
@app.post("/api/simulation/capacity")
async def capacity(req: CapacityRequest):
    try: result=sim.set_capacity(req.capacity)
    except ValueError as e: raise HTTPException(400,str(e))
    await broadcast(); return result
@app.get("/api/policies")
def get_policies():
    conn=db(); history=[json.loads(row[0]) for row in conn.execute("SELECT payload FROM policy_events ORDER BY id DESC LIMIT 100").fetchall()]; conn.close()
    return {"policies":sim.policies,"history":history,"defaults":DEFAULT_POLICIES}
@app.post("/api/policies/apply")
async def policies(req: PolicyRequest):
    try: result=sim.apply_policies(req.policies)
    except (ValueError,KeyError,TypeError) as e: raise HTTPException(400,str(e))
    event=sim.history[-1]
    conn=db(); conn.execute("INSERT INTO policies(created_at,payload) VALUES(?,?)",(time.time(),json.dumps(sim.policies))); conn.execute("INSERT INTO policy_events(created_at,payload) VALUES(?,?)",(time.time(),json.dumps(event))); conn.commit(); conn.close()
    await broadcast(); return result
@app.post("/api/policies/rollback")
async def rollback():
    try: result=sim.rollback()
    except ValueError as e: raise HTTPException(409,str(e))
    event=sim.policy_log[-1]
    conn=db(); conn.execute("INSERT INTO policies(created_at,payload) VALUES(?,?)",(event["time"],json.dumps(sim.policies))); conn.execute("INSERT INTO policy_events(created_at,payload) VALUES(?,?)",(event["time"],json.dumps(event))); conn.commit(); conn.close()
    await broadcast(); return result
@app.post("/api/intents")
async def activate(req: IntentRequest):
    try: result=sim.activate_intent(req.name,req.duration)
    except ValueError as e: raise HTTPException(400,str(e))
    await broadcast(); return result
@app.delete("/api/intents/{intent_id}")
async def cancel(intent_id:str):
    result=sim.cancel_intent(intent_id); await broadcast(); return result
@app.patch("/api/sessions/classification")
async def correct(req: CorrectionRequest):
    if req.traffic_class not in CLASSES: raise HTTPException(400,"Unknown traffic class")
    session=next((x for x in sim.sessions if x["id"]==req.session_id),None)
    if not session: raise HTTPException(404,"Session not found")
    session["class"]=req.traffic_class; session["qos_class"]=req.traffic_class; session["confidence"]=1.0; session["manual_override"]=True; session["classification_rule"]="Manually corrected by operator"
    sim.timeline.append({"time":time.time(),"event":f"Manual classification · {session['name']} → {req.traffic_class}","type":"policy"})
    await broadcast(); return sim.snapshot()
@app.post("/api/classify")
def classify(req: ClassificationRequest):
    try:
        return classify_flow(FlowMetadata.from_mapping(req.metadata))
    except (TypeError,ValueError) as e:
        raise HTTPException(400,str(e))

@app.post("/api/experiments/run")
def run_experiment(req: ExperimentRequest):
    if req.scenario not in SCENARIOS: raise HTTPException(400,"Unknown scenario")
    try:
        result=run_comparison(req.scenario,req.seed,req.ticks)
    except Exception as e: raise HTTPException(500,str(e))
    result["created_at"]=time.time()
    conn=db(); cur=conn.execute("INSERT INTO experiments(created_at,scenario,payload) VALUES(?,?,?)",(result["created_at"],req.scenario,json.dumps(result))); result["id"]=cur.lastrowid; conn.commit(); conn.close()
    return result
@app.get("/api/experiments")
def experiments():
    conn=db(); rows=conn.execute("SELECT id,created_at,scenario,payload FROM experiments ORDER BY id DESC LIMIT 100").fetchall(); conn.close()
    return [{"id":r[0],"created_at":r[1],"scenario":r[2],**json.loads(r[3])} for r in rows]
@app.get("/api/experiments/{experiment_id}")
def experiment(experiment_id:int):
    conn=db(); row=conn.execute("SELECT payload FROM experiments WHERE id=?",(experiment_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Experiment not found")
    return json.loads(row[0])
@app.get("/api/experiments/{experiment_id}/report.json")
def report_json(experiment_id:int): return experiment(experiment_id)
@app.get("/api/experiments/{experiment_id}/report.csv")
def report_csv(experiment_id:int):
    result=experiment(experiment_id); out=io.StringIO(); w=csv.writer(out); w.writerow(["scope","policy","tick","metric","value"])
    for policy in ("baseline","adaptive"):
        for key,value in result[policy].items(): w.writerow(["summary",policy,"",key,value])
        for row in result.get("timeseries",{}).get(policy,[]):
            tick=row.get("tick","")
            for key,value in row.items():
                if isinstance(value,(int,float)) and not isinstance(value,bool): w.writerow(["timeseries",policy,tick,key,value])
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",headers={"Content-Disposition":f"attachment; filename=qos-experiment-{experiment_id}.csv"})
@app.get("/api/enforcement/status")
def enforcement_status(): return tc.status()
@app.post("/api/enforcement/apply")
def enforcement_apply(req:EnforcementRequest):
    try:return tc.apply(req.interface,req.confirmed,req.capacity_mbps,sim.policies)
    except ValueError as e:raise HTTPException(400,str(e))
@app.post("/api/enforcement/rollback")
def enforcement_rollback():
    try:return tc.rollback()
    except ValueError as e:raise HTTPException(409,str(e))
@app.websocket("/ws/telemetry")
async def telemetry(ws:WebSocket):
    await ws.accept(); clients.add(ws)
    await ws.send_json({"type":"telemetry","data":sim.snapshot()})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)
