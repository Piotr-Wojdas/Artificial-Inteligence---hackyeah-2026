"""Train the battery agent and export it to policy.npz (plain numpy, see policy.py).

    uv sync --group rl
    uv run python -m solary.battery train --steps 3000000

Two stages, both on real prices and weather from TRAIN in four Polish cities and random houses
(battery losses and wear, tariff, a strong grid or one that trips the inverter):
  1. Imitation: the network learns the decisions of the nonlinear planner (strategies.dp_mpc, dynamic
     programming on the forecasts every hour) on a few hundred random weeks, and the value of those
     weeks: a good starting point instead of random play.
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
    """One random week run by the nonlinear planner: observations, its actions, discounted rewards-to-go."""
    from . import strategies as S
    from .env import REWARD_SCALE, BatteryEnv

    env = BatteryEnv(_PLACES)
    env.reset(seed=seed)
    sc, soc0 = env.sc, env.soc
    r = S.dp_mpc(sc, soc0)
    before = np.r_[soc0, r.soc[:-1]]
    obs = np.stack([observe(sc, t, before[i]) for i, t in enumerate(range(sc.start, sc.end))])
    t = slice(sc.start, sc.end)
    cost = r.grid_import * sc.buy[t] - r.grid_export * sc.sell[t] + r.wear
    reward = (env.baseline - cost) * REWARD_SCALE
    ret = np.zeros_like(reward)
    acc = 0.0
    for i in range(len(reward) - 1, -1, -1):
        acc = reward[i] + GAMMA * acc
        ret[i] = acc
    return obs, r.actions, ret


def demonstrations(places, episodes: int, seed: int = 0, workers: int = 4, cache: Path | None = None, log=print) -> dict:
    """`episodes` random weeks run by the planner (cached in `cache`, an .npz, when given)."""
    if cache is not None and cache.is_file():
        with np.load(cache) as f:
            if int(f["episodes"]) == episodes and int(f["seed"]) == seed:
                log(f"{episodes} planner weeks from {cache}")
                return {"obs": f["obs"], "actions": f["actions"], "returns": f["returns"]}
    t0 = time.time()
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(places,)) as pool:
        parts = list(pool.map(_demo, range(seed + 1_000_000, seed + 1_000_000 + episodes), chunksize=4))
    obs, act, ret = (np.concatenate(x) for x in zip(*parts))
    log(f"{episodes} planner weeks, {len(act):,} decisions in {time.time() - t0:.0f}s; actions used: "
        + ", ".join(f"{ACTIONS[a]} {np.mean(act == a):.0%}" for a in range(len(ACTIONS)) if np.any(act == a)))
    if cache is not None:
        np.savez(cache, obs=obs, actions=act, returns=ret, episodes=episodes, seed=seed)
    return {"obs": obs, "actions": act, "returns": ret}


def pretrain(model, venv, demo: dict, epochs: int = 40, batch: int = 4096, lr: float = 1e-3, log=print) -> None:
    """Fit the actor to the planner's actions (cross-entropy) and the critic to the returns."""
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
            log(f"  imitation epoch {epoch + 1}: agrees with the planner on {acc:.0%} of decisions")


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


def train(steps: int = 3_000_000, out: Path = POLICY_PATH, cfg: Config = CONFIG, n_envs: int = 8, seed: int = 0,
          imitation_weeks: int = 800, critic_warmup: int = 300_000, demo_cache: Path | None = None,
          learning_rate: float | None = None, clip_range: float | None = None, log=print) -> Path:
    """Imitation (if `imitation_weeks`), then PPO. After imitation the observation normalisation is
    frozen (the imitated network depends on it), the critic first learns alone for `critic_warmup`
    steps with the policy frozen, and PPO then moves the policy in small, entropy-free steps."""
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
    top = learning_rate or (5e-5 if warm else 3e-4)         # learning rate falls linearly to a tenth
    model = PPO("MlpPolicy", venv, n_steps=2048, batch_size=4096, n_epochs=6, gamma=GAMMA, gae_lambda=0.95,
                learning_rate=lambda left: top * (0.1 + 0.9 * left), ent_coef=0.0 if warm else 0.01,
                clip_range=clip_range or (0.1 if warm else 0.2), target_kl=0.02 if warm else None, max_grad_norm=0.5,
                policy_kwargs={"net_arch": {"pi": [256, 256], "vf": [256, 256]}}, seed=seed, verbose=0, device="cpu")
    actor = [*model.policy.mlp_extractor.policy_net.parameters(), *model.policy.action_net.parameters()]

    def freeze_actor(frozen: bool) -> None:
        for prm in actor:
            prm.requires_grad_(not frozen)
    meta = {"seed": seed, "train": [str(d) for d in TRAIN], "locations": list(LOCATIONS), "imitation_weeks": imitation_weeks,
            "teacher": "dp_mpc", "learning_rate": top}
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
        pretrain(model, venv, demonstrations(places, imitation_weeks, seed, cache=demo_cache, log=log), log=log)
        venv.training = False                                 # keep the normalisation the network was taught with
        freeze_actor(critic_warmup > 0)
        checkpoint(0)

    class Every(BaseCallback):
        def __init__(self):
            super().__init__()
            self.next = 200_000

        def _on_step(self) -> bool:
            if warm and critic_warmup and self.num_timesteps >= critic_warmup and not actor[0].requires_grad:
                freeze_actor(False)
                log(f"{self.num_timesteps:>9,} steps: critic warmed up, the policy starts to learn")
            if self.num_timesteps >= self.next:
                training, venv.training = venv.training, False
                checkpoint(self.num_timesteps)
                venv.training = training
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
