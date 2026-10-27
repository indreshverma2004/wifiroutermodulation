"""Link-capacity estimation from sampled probe observations."""
from collections import deque
from statistics import median


class CapacityEstimator:
    """Robust EWMA over observations, with faster downward response.

    The estimator knows only probe samples. A production deployment should
    replace the simulator's probe source with active-probe or interface data.
    """
    def __init__(self, initial: float = 100.0, alpha_up: float = 0.22, alpha_down: float = 1.0):
        if not 0 < alpha_up <= 1 or not 0 < alpha_down <= 1:
            raise ValueError("Estimator alphas must be in (0, 1]")
        self.estimate = float(initial)
        self.alpha_up = alpha_up
        self.alpha_down = alpha_down
        self.samples: deque[float] = deque(maxlen=5)
        self.observation_count = 0

    def observe(self, sample_mbps: float) -> float:
        sample = float(sample_mbps)
        if sample <= 0:
            raise ValueError("Capacity observations must be positive")
        self.samples.append(sample)
        self.observation_count += 1
        robust_sample = median(self.samples)
        # A large downward step is actionable evidence; don't let old high samples
        # in the rolling window hide a sudden capacity collapse for several ticks.
        if sample < self.estimate * 0.75:
            robust_sample = sample
        alpha = self.alpha_down if robust_sample < self.estimate else self.alpha_up
        self.estimate += alpha * (robust_sample - self.estimate)
        return self.estimate
