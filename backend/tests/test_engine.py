import copy
import time
import pytest
from qos.engine import CapacityEstimator, CLASSES, DEFAULT_POLICIES, Simulator, jain_fairness, weighted_allocate

def test_capacity_estimator_tracks_sudden_drop():
    est = CapacityEstimator(100)
    assert est.observe(20) == 20
    assert est.observe(20) == 20

def test_adaptive_allocation_respects_capacity_and_protects_interactive():
    demand = {"Video call": 15, "Downloads": 90}
    policy = {"Video call": DEFAULT_POLICIES["Video call"], "Downloads": DEFAULT_POLICIES["Downloads"]}
    allocation = weighted_allocate(demand, 20, policy, True)
    assert sum(allocation.values()) <= 20 + 1e-8
    assert allocation["Video call"] >= 4
    assert allocation["Downloads"] > 0

def test_priority_weight_changes_adaptive_service():
    demand = {"Video call": 20, "Downloads": 40}
    policy = {"Video call": DEFAULT_POLICIES["Video call"].copy(), "Downloads": DEFAULT_POLICIES["Downloads"].copy()}
    before = weighted_allocate(demand, 30, policy, True)
    policy["Video call"]["priority"] = 8
    after = weighted_allocate(demand, 30, policy, True)
    assert after["Downloads"] > before["Downloads"]

def test_bulk_progress_and_no_starvation():
    demand = {"Downloads": 60, "Cloud backup": 40}
    allocation = weighted_allocate(demand, 20, {k: DEFAULT_POLICIES[k] for k in demand}, True)
    assert all(allocation[k] > 0 for k in demand)

def test_jain_fairness_correct():
    assert jain_fairness([10, 10]) == pytest.approx(1)
    assert jain_fairness([10, 0]) == pytest.approx(.5)
    assert jain_fairness([]) == 0

def test_policy_validation_and_rollback():
    sim = Simulator()
    bad = copy.deepcopy(DEFAULT_POLICIES); bad["Voice"]["priority"] = 0
    with pytest.raises(ValueError): sim.apply_policies(bad)
    changed = copy.deepcopy(DEFAULT_POLICIES); changed["Voice"]["weight"] = 10
    sim.apply_policies(changed)
    assert sim.policies["Voice"]["weight"] == 10
    sim.rollback()
    assert sim.policies["Voice"]["weight"] == DEFAULT_POLICIES["Voice"]["weight"]

def test_intent_activation_cancellation_and_expiration():
    sim = Simulator()
    sim.activate_intent("Prioritize Work Call", 15)
    assert len(sim.intents) == 1
    intent = sim.intents[0]
    sim.intents[0]["expires_at"] = time.time() - 1
    sim._effective_policies()
    assert not sim.intents
    sim.activate_intent("Gaming", 15)
    sim.cancel_intent(sim.intents[0]["id"])
    assert not sim.intents

def test_reproducible_experiment():
    def run(adaptive):
        sim = Simulator(); sim.start("work_call_iso", adaptive, seed=77)
        return [sim.step()["metrics"] for _ in range(15)]
    first, second = run(True), run(True)
    assert [{k:v for k,v in m.items() if k != 'time'} for m in first] == [{k:v for k,v in m.items() if k != 'time'} for m in second]

def test_workload_baseline_adaptive_same_seed_and_different_metrics():
    sim_a, sim_b = Simulator(), Simulator()
    sim_a.start("gaming_streams", False, 123); sim_b.start("gaming_streams", True, 123)
    a = [sim_a.step()["metrics"] for _ in range(20)]
    b = [sim_b.step()["metrics"] for _ in range(20)]
    assert [x["offered"] for x in a] == [x["offered"] for x in b]
    assert a[-1]["latency"] != b[-1]["latency"]

def test_capacity_drop_happens_at_tick_eight():
    sim = Simulator(); sim.start("capacity_drop", True, 42)
    for _ in range(6): sim.step()
    assert sim.capacity == 100
    snap = sim.step()
    assert snap["metrics"]["capacity"] == 20
    assert 18.4 <= snap["metrics"]["estimated_capacity"] <= 21.6
