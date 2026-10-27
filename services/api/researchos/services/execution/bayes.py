"""Bayesian-optimization sampler `gp_ei` for study search (ADR-168): a Gaussian process (Matern 5/2 + white noise) on
inputs scaled to [0, 1] on each parameter's own scale (log ranges in log space), the objective standardized; expected
improvement over a seeded Sobol pool of feasible candidates; a batch is the n highest-EI distinct candidates.

Model numbers (mean, latent sd, EI) are estimates that guide where to look next, never results. Too little history for a
model (< MIN_HISTORY successes) falls back to the plain Sobol batch, and says so.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from . import sampling

MIN_HISTORY = 4
POOL = 1024


def unit(spec: dict[str, Any], value: Any) -> float:
    if "choices" in spec:
        c = spec["choices"]
        return float(c.index(value)) / max(1, len(c) - 1)
    lo, hi = float(spec["low"]), float(spec["high"])
    if spec.get("log"):
        return (math.log(float(value)) - math.log(lo)) / (math.log(hi) - math.log(lo))
    return (float(value) - lo) / (hi - lo) if hi > lo else 0.0


def _constant(spec: dict[str, Any]) -> bool:
    return len(spec["choices"]) < 2 if "choices" in spec else float(spec["low"]) == float(spec["high"])


def _model(x: Any, y: Any, seed: int) -> Any:
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

    kernel = ConstantKernel(1.0, (1e-2, 1e2)) * Matern(
        length_scale=[0.3] * x.shape[1], length_scale_bounds=(1e-2, 1e1), nu=2.5
    ) + WhiteKernel(1e-2, (1e-6, 1.0))
    return GaussianProcessRegressor(kernel=kernel, random_state=seed, n_restarts_optimizer=2).fit(x, y)


def gp_ei_batch(
    space: dict[str, Any],
    fixed: dict[str, Any],
    history: list[tuple[dict[str, Any], float]],
    n: int,
    seed: int,
    direction: str,
    avoid: Sequence[str] = (),
    constraints: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(points, info). info: model used or fallback reason, and per point the GP mean/sd (objective units) and EI."""
    import numpy as np
    from scipy.stats import norm

    pool = sampling.qmc_batch(space, fixed, POOL, seed=seed, skip=0, avoid=avoid, constraints=constraints)
    if not pool:  # sampling found no feasible point: not a proof of infeasibility (review #61)
        return [], {"model": None, "reason": f"no feasible candidate among {POOL * 4} Sobol draws"}
    names = [k for k in sorted(space) if not _constant(space[k])]  # fixed dimensions carry no information (#58)
    if len(history) < MIN_HISTORY or not names:
        why = "every parameter is fixed" if not names else f"fewer than {MIN_HISTORY} successful runs"
        return pool[:n], {"model": None, "reason": f"{why}: Sobol batch"}
    x = np.array([[unit(space[k], p[k]) for k in names] for p, _ in history])
    sign = 1.0 if direction == "max" else -1.0
    raw = sign * np.array([v for _, v in history], dtype=float)
    mu_y, sd_y = raw.mean(), raw.std() or 1.0
    gp = _model(x, (raw - mu_y) / sd_y, seed)
    cand = np.array([[unit(space[k], p[k]) for k in names] for p in pool])
    mean, total_sd = gp.predict(cand, return_std=True)
    # Noise-aware EI (review #60): uncertainty of the underlying objective (white noise removed) against the best
    # DENOISED value at the observed points, not the luckiest noisy observation.
    noise = float(gp.kernel_.k2.noise_level)
    sd = np.sqrt(np.maximum(total_sd**2 - noise, 0.0))
    best = float(np.max(gp.predict(x)))
    z = (mean - best) / np.maximum(sd, 1e-12)
    ei = np.where(sd > 1e-12, (mean - best) * norm.cdf(z) + sd * norm.pdf(z), np.maximum(mean - best, 0.0))
    picked: list[int] = []
    seen: set[tuple[Any, ...]] = set()
    for i in np.argsort(-ei):
        key = tuple(pool[int(i)][k] for k in names)
        if key not in seen:
            seen.add(key)
            picked.append(int(i))
        if len(picked) == n:
            break
    detail = [{"mean": float(sign * (mean[i] * sd_y + mu_y)), "sd": float(sd[i] * sd_y), "ei": float(ei[i] * sd_y)}
              for i in picked]
    return [pool[i] for i in picked], {"model": str(gp.kernel_), "points": detail, "max_ei": detail[0]["ei"],
                                       "noise_sd": float(np.sqrt(noise)) * sd_y,
                                       "incumbent": float(sign * (best * sd_y + mu_y))}  # denoised best (#60)
