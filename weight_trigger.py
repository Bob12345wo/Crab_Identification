"""Stable empty/load transitions, requiring removal before every measurement."""

import math


class WeightTrigger:
    def __init__(self, load_g=20.0, empty_g=5.0, windows=2):
        if not (math.isfinite(load_g) and math.isfinite(empty_g) and 0 <= empty_g < load_g and windows >= 1):
            raise ValueError("Invalid trigger thresholds")
        self.load_g = load_g
        self.empty_g = empty_g
        self.windows = windows
        self.state = "waiting_empty"
        self.count = 0

    def update(self, weight):
        value = weight.get("weight_g")
        if not weight.get("ok") or value is None or not math.isfinite(value):
            self.count = 0
            return False
        matched = (0 <= value <= self.empty_g if self.state == "waiting_empty" else value >= self.load_g)
        self.count = self.count + 1 if matched else 0
        if self.count < self.windows:
            return False
        self.count = 0
        if self.state == "waiting_empty":
            self.state = "waiting_load"
            return False
        self.state = "waiting_empty"
        return True
