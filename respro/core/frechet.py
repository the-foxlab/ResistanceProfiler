"""Sharp Fréchet intersection bounds shared by same-codon and formula co-occurrence."""

from __future__ import annotations

from collections.abc import Sequence

from respro.config.cli_settings import CLI_CONFIG

DEFAULT_FRECHET_EPS = CLI_CONFIG.codon.frechet_epsilon


def frechet_and_bound(
    q_values: Sequence[float],
    min_fraction: float,
    eps: float = DEFAULT_FRECHET_EPS,
) -> tuple[float, float, float, bool]:
    """Compute sharp Fréchet intersection bounds ``(lower, upper, forced_fraction, accepted)``."""
    if not q_values:
        raise ValueError('AND clause requires at least one member frequency')
    k = len(q_values)
    upper = min(q_values)
    # Clamp lower <= upper: exact arithmetic guarantees this, but float sum can overshoot by 1 ULP.
    lower = min(max(0.0, sum(q_values) - (k - 1)), upper)
    forced_fraction = lower / upper if upper > 0 else 0.0
    accepted = lower > eps and forced_fraction >= min_fraction - eps
    return lower, upper, forced_fraction, accepted


def frechet_factor_bounds(
    factor_bounds: Sequence[tuple[float, float]],
    min_fraction: float,
    eps: float = DEFAULT_FRECHET_EPS,
) -> tuple[float, float, float, bool]:
    """Combine per-factor ``(lower, upper)`` bounds across disjoint phase factors."""
    if not factor_bounds:
        raise ValueError('Factor combination requires at least one phase factor')
    m = len(factor_bounds)
    upper = min(fu for _, fu in factor_bounds)
    lower = min(max(0.0, sum(fl for fl, _ in factor_bounds) - (m - 1)), upper)
    forced_fraction = lower / upper if upper > 0 else 0.0
    accepted = lower > eps and forced_fraction >= min_fraction - eps
    return lower, upper, forced_fraction, accepted
