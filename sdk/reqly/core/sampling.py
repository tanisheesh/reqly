from __future__ import annotations

import random
import threading


class Sampler:
    """Decides whether a given request should be kept.

    Unsampled requests still increment ``observed_count`` so that volume and
    rate metrics can be corrected downstream by ``1 / sample_rate``. Default
    sample_rate is 1.0 (no sampling) -- this exists as a documented config
    knob, not because this project's traffic needs it.
    """

    def __init__(self, sample_rate: float = 1.0) -> None:
        self.sample_rate = max(0.0, min(1.0, sample_rate))
        self._lock = threading.Lock()
        self.observed_count = 0
        self.sampled_count = 0

    def should_sample(self) -> bool:
        with self._lock:
            self.observed_count += 1
            if self.sample_rate >= 1.0:
                self.sampled_count += 1
                return True
            keep = random.random() < self.sample_rate
            if keep:
                self.sampled_count += 1
            return keep

    def stats(self) -> dict:
        with self._lock:
            return {
                "observed_count": self.observed_count,
                "sampled_count": self.sampled_count,
                "sample_rate": self.sample_rate,
            }
