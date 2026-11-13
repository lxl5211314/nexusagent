"""Rank fusion. RRF is the default; weighted score fusion is kept for comparison."""

from __future__ import annotations

from collections import defaultdict


def rrf_fuse(
    rankings: dict[str, list[str]],
    k: int = 60,
    weights: dict[str, float] | None = None,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion.

    ``score(d) = Σ_c w_c / (k + rank_c(d))`` with ranks starting at 1.

    RRF is rank-based, so channels with incompatible score scales (cosine in
    [-1, 1] vs unbounded BM25) need no normalisation — that is exactly why it is
    preferred over weighted score fusion here.
    """
    weights = weights or {}
    scores: dict[str, float] = defaultdict(float)
    for channel, ids in rankings.items():
        weight = weights.get(channel, 1.0)
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] += weight / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def weighted_fuse(
    rankings: dict[str, list[str]],
    score_map: dict[str, float],
    weights: dict[str, float],
) -> list[tuple[str, float]]:
    """Min-max normalised score fusion — kept as the baseline RRF is compared to."""
    fused: dict[str, float] = defaultdict(float)
    for channel, ids in rankings.items():
        if not ids:
            continue
        values = [score_map.get(f"{channel}:{cid}", 0.0) for cid in ids]
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        weight = weights.get(channel, 1.0)
        for cid, value in zip(ids, values, strict=False):
            fused[cid] += weight * (value - lo) / span
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)
