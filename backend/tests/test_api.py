import json
import pytest
from fastapi.testclient import TestClient
from qos import main as app_module

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "test.sqlite3")
    with TestClient(app_module.app) as client:
        yield client

def test_health_state_and_websocket(client):
    assert client.get('/api/health').json()['simulated'] is True
    assert client.get('/api/state').status_code == 200
    assert client.post('/api/simulation/start',json={'scenario':'work_call_iso','adaptive':True,'seed':12}).status_code == 200
    with client.websocket_connect('/ws/telemetry') as ws:
        event = ws.receive_json()
        assert event['type'] == 'telemetry'
        assert event['data']['simulated'] is True
        next_event = ws.receive_json()
        assert next_event['data']['metrics']['tick'] > event['data']['metrics']['tick']

def test_live_controls_policy_and_intent(client):
    started = client.post('/api/simulation/start', json={'scenario':'capacity_drop','adaptive':True,'seed':4})
    assert started.status_code == 200
    assert started.json()['metrics']['tick'] == 1
    assert client.post('/api/simulation/pause').json()['status'] == 'paused'
    assert client.post('/api/simulation/resume').json()['status'] == 'running'
    changed=client.post('/api/simulation/capacity',json={'capacity':20})
    assert changed.status_code == 200 and changed.json()['metrics']['capacity'] == 20
    policies = client.get('/api/policies').json()['policies']
    policies['Voice']['weight'] = 9
    assert client.post('/api/policies/apply', json={'policies':policies}).status_code == 200
    assert client.post('/api/policies/rollback').status_code == 200
    assert len(client.get('/api/policies').json()['history']) >= 2
    assert client.post('/api/intents', json={'name':'Prioritize Work Call','duration':15}).status_code == 200
    assert client.get('/api/state').json()['intents']

def test_experiment_storage_and_reports(client):
    req={'scenario':'work_call_iso','seed':88,'ticks':10}
    a=client.post('/api/experiments/run',json=req); b=client.post('/api/experiments/run',json=req)
    assert a.status_code == 200
    first=a.json()
    assert first['adaptive']==b.json()['adaptive']
    assert client.get('/api/experiments').status_code == 200
    assert 'baseline' in client.get(f"/api/experiments/{first['id']}/report.json").json()
    assert first['baseline']['adaptation_time_s'] == first['adaptive']['adaptation_time_s'] == 0
    assert 'text/csv' in client.get(f"/api/experiments/{first['id']}/report.csv").headers['content-type']
