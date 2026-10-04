"""The trained agent in plain numpy: a small neural network (observation -> one of model.ACTIONS).
Training (train.py) needs torch; running the agent does not."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .model import ACTIONS, OBS_SIZE

POLICY_PATH = Path(__file__).with_name("policy.npz")


class Policy:
    def __init__(self, path: Path = POLICY_PATH):
        with np.load(path) as f:
            self.mean, self.var = f["obs_mean"], f["obs_var"]
            self.clip, self.eps = float(f["clip_obs"]), float(f["epsilon"])
            n = int(f["n_layers"])
            self.layers = [(f[f"w{i}"], f[f"b{i}"]) for i in range(n)]
            self.meta = json.loads(str(f["meta"]))
        if self.mean.shape != (OBS_SIZE,) or tuple(self.meta["actions"]) != ACTIONS:
            raise ValueError(f"{path} was trained for another observation or set of actions: retrain it")

    def logits(self, obs: np.ndarray) -> np.ndarray:
        x = np.clip((obs - self.mean) / np.sqrt(self.var + self.eps), -self.clip, self.clip)
        for w, b in self.layers[:-1]:
            x = np.tanh(x @ w + b)
        w, b = self.layers[-1]
        return x @ w + b

    def act(self, obs: np.ndarray) -> int:
        return int(np.argmax(self.logits(obs)))


def available(path: Path = POLICY_PATH) -> bool:
    return path.is_file()
