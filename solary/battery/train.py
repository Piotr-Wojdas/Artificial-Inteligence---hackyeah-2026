"""Train the battery agent and export it to policy.npz (plain numpy, see policy.py).

    uv sync --group rl
    uv run python -m solary.battery train --steps 2000000

Two stages, both on real prices and weather from TRAIN in four Polish cities:
  1. Imitation: the network learns the decisions MPC (strategies.mpc) makes on a few hundred
     random weeks, and the value of those weeks: a good starting point instead of random play.
  2. Reinforcement learning (PPO): the agent runs its own week after week on random houses and
     improves on what it imitated. Every 200,000 steps it is scored on fixed validation weeks;
     the best version is exported.
evaluate.py then tests the agent on TEST, a year it has never seen.
"""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np

from ..config import CONFIG, Config
from .data import history
from .model import ACTIONS, OBS_SIZE, cost_without_battery, observe
from .policy import POLICY_PATH, Policy

TRAIN = (date(2024, 7, 1), date(2025, 6, 30))
TEST = (date(2025, 7, 1), date(2026, 6, 30))
LOCATIONS = {"Katowice": (50.26, 19.02), "Warszawa": (52.23, 21.01), "Gdańsk": (54.35, 18.65),
             "Wrocław": (51.11, 17.03)}
GAMMA = 0.995                         # 15-minute steps: decisions look about two days ahead
VALIDATION_SEEDS = range(900_000, 900_024)


def training_places(cfg: Config = CONFIG):
    from .env import Place
    return [Place.build(history(lat, lon, *TRAIN, cfg)) for lat, lon in LOCATIONS.values()]


# ------------------------------------------------------------------ 1. imitation of MPC
_PLACES = None


def _init(places):
    global _PLACES
    _PLACES = places


def _demo(seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One random week run by MPC: observations, MPC's actions, discounted rewards-to-go."""
    from . import strategies as S
    from .env import REWARD_SCALE, BatteryEnv

    env = BatteryEnv(_PLACES)
    env.reset(seed=seed)
    sc, soc0 = env.sc, env.soc
    r = S.mpc(sc, soc0)
    before = np.r_[soc0, r.soc[:-1]]
    obs = np.stack([observe(sc, t, before[i]) for i, t in enumerate(range(sc.start, sc.end))])
    t = slice(sc.start, sc.end)
    cost = (r.grid_import * sc.buy[t] - r.grid_export * sc.sell[t] + r.discharge * sc.battery.wear_zl_per_kwh)
    reward = (cost_without_battery(sc) - cost) * REWARD_SCALE
    ret = np.zeros_like(reward)
    acc = 0.0
    for i in range(len(reward) - 1, -1, -1):
        acc = reward[i] + GAMMA * acc
        ret[i] = acc
    return obs, r.actions, ret


def demonstrations(places, episodes: int, seed: int = 0, workers: int = 4, log=print) -> dict:
    t0 = time.time()
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(places,)) as pool:
        parts = list(pool.map(_demo, range(seed + 1_000_000, seed + 1_000_000 + episodes), chunksize=4))
    obs, act, ret = (np.concatenate(x) for x in zip(*parts))
    log(f"{episodes} MPC weeks, {len(act):,} decisions in {time.time() - t0:.0f}s; actions used: "
        + ", ".join(f"{ACTIONS[a]} {np.mean(act == a):.0%}" for a in range(len(ACTIONS)) if np.any(act == a)))
    return {"obs": obs, "actions": act, "returns": ret}


def pretrain(model, venv, demo: dict, epochs: int = 40, batch: int = 4096, lr: float = 1e-3, log=print) -> None:
    """Fit the actor to MPC's actions (cross-entropy) and the critic to the returns (squared error)."""
    import torch
    import torch.nn.functional as F

    venv.obs_rms.mean = demo["obs"].mean(axis=0).astype(np.float64)
    venv.obs_rms.var = demo["obs"].var(axis=0).astype(np.float64)
    venv.obs_rms.count = float(len(demo["obs"]))
    x = torch.as_tensor(venv.normalize_obs(demo["obs"]), dtype=torch.float32)
    a = torch.as_tensor(demo["actions"], dtype=torch.long)
    g = torch.as_tensor(demo["returns"], dtype=torch.float32)
    policy = model.policy
    opt = torch.optim.Adam(policy.parameters(), lr=lr)
    n = len(a)
    for epoch in range(epochs):
        order = torch.randperm(n)
        for i in range(0, n, batch):
            idx = order[i:i + batch]
            logits = policy.get_distribution(x[idx]).distribution.logits
            loss = F.cross_entropy(logits, a[idx]) + 0.5 * F.mse_loss(policy.predict_values(x[idx]).squeeze(-1), g[idx]) / 100
            opt.zero_grad()
            loss.backward()
            opt.step()
        if epoch % 10 == 9 or epoch == epochs - 1:
            with torch.no_grad():
                acc = (policy.get_distribution(x).distribution.logits.argmax(-1) == a).float().mean().item()
            log(f"  imitation epoch {epoch + 1}: agrees with MPC on {acc:.0%} of decisions")


# ------------------------------------------------------------------ 2. reinforcement learning
def validation(places) -> list:
    from .env import BatteryEnv
    env = BatteryEnv(places)
    out = []
    for seed in VALIDATION_SEEDS:
        env.reset(seed=seed)
        out.append((env.sc, env.soc, float(cost_without_battery(env.sc).sum())))
    return out


def score(policy: Policy, weeks: list) -> float:
    """Mean zł saved per week (vs no battery) on the validation weeks."""
    from . import strategies as S
    return float(np.mean([base - S.agent(sc, policy, soc0).cost for sc, soc0, base in weeks]))


def train(steps: int = 2_000_000, out: Path = POLICY_PATH, cfg: Config = CONFIG, n_envs: int = 8, seed: int = 0,
          imitation_weeks: int = 320, log=print) -> Path:
    import torch
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
    from stable_baselines3.common.vec_env import SubprocVecEnv, VecMonitor, VecNormalize

    from .env import BatteryEnv

    torch.set_num_threads(1)
    places = training_places(cfg)
    weeks = validation(places)
    venv = VecMonitor(SubprocVecEnv([lambda i=i: BatteryEnv(places, seed=seed + i) for i in range(n_envs)]))
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0, gamma=GAMMA)
    warm = imitation_weeks > 0
    model = PPO("MlpPolicy", venv, n_steps=1024, batch_size=1024, n_epochs=10, gamma=GAMMA, gae_lambda=0.95,
                learning_rate=1e-4 if warm else 3e-4, ent_coef=0.002 if warm else 0.01,
                clip_range=0.1 if warm else 0.2, policy_kwargs={"net_arch": [128, 128]}, seed=seed, verbose=0,
                device="cpu")
    meta = {"seed": seed, "train": [str(d) for d in TRAIN], "locations": list(LOCATIONS), "imitation_weeks": imitation_weeks}
    tmp = Path(tempfile.mkdtemp()) / "policy.npz"
    best = {"score": -np.inf, "steps": 0}

    def checkpoint(done_steps: int) -> None:
        export(model, venv, tmp, meta)
        s = score(Policy(tmp), weeks)
        mark = ""
        if s > best["score"]:
            best.update(score=s, steps=done_steps)
            export(model, venv, out, {**meta, "steps": done_steps, "validation_zl_per_week": round(s, 2),
                                      "trained_at": time.strftime("%Y-%m-%d %H:%M")})
            mark = "  <- best, saved"
        log(f"{done_steps:>9,} steps: saves {s:6.2f} zł a week on the validation weeks{mark}")

    if warm:
        pretrain(model, venv, demonstrations(places, imitation_weeks, seed, log=log), log=log)
        checkpoint(0)

    class Every(BaseCallback):
        def __init__(self):
            super().__init__()
            self.next = 200_000

        def _on_step(self) -> bool:
            if self.num_timesteps >= self.next:
                venv.training = False
                checkpoint(self.num_timesteps)
                venv.training = True
                self.next += 200_000
            return True

    t0 = time.time()
    model.learn(total_timesteps=steps, callback=Every())
    checkpoint(steps)
    venv.close()
    log(f"done in {time.time() - t0:.0f}s; best: {best['score']:.2f} zł a week after {best['steps']:,} steps -> {out}")
    return out


def export(model, venv, path: Path, meta: dict) -> None:
    """The actor network and the observation normalisation as plain arrays (see policy.Policy)."""
    import torch.nn as nn

    linear = [m for m in model.policy.mlp_extractor.policy_net if isinstance(m, nn.Linear)] + [model.policy.action_net]
    arrays = {}
    for i, layer in enumerate(linear):
        arrays[f"w{i}"] = layer.weight.detach().cpu().numpy().T.astype(np.float32)
        arrays[f"b{i}"] = layer.bias.detach().cpu().numpy().astype(np.float32)
    assert arrays["w0"].shape[0] == OBS_SIZE
    np.savez_compressed(path, obs_mean=venv.obs_rms.mean.astype(np.float32), obs_var=venv.obs_rms.var.astype(np.float32),
                        clip_obs=np.float32(venv.clip_obs), epsilon=np.float32(venv.epsilon), n_layers=len(linear),
                        meta=json.dumps({**meta, "actions": list(ACTIONS), "obs_size": OBS_SIZE}), **arrays)
