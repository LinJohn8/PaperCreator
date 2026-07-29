"""Density heatmaps over the projected landscape.

Three products, all computed from the same projected coordinates:

* **Base density grid** - how crowded each region of the map is. This is the layer
  that makes "where is the literature dense, where is it thin" visible at a
  glance, and it is the input to sparse-region gap detection.
* **Keyword layers** - the same grid restricted to papers containing a term, so
  the UI can answer "where do the papers about *federated learning* sit?" without
  a round trip per keyword.
* **Z slices** - because the map is 3D, a 2D grid alone hides structure. Slices
  along z give the frontend cross-sections to fade between.

Method: Gaussian kernel density estimation on a regular grid. Not a plain 2D
histogram, because bin edges create visual artefacts (a cluster straddling a
boundary looks like two), and gap detection on histogram counts finds empty bins
rather than genuinely empty regions. Bandwidth defaults to Scott's rule.

Implemented directly on numpy rather than via scipy/sklearn's KDE so the base
install has no extra dependency and the grid evaluation stays vectorised.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..core.logging_setup import get_logger
from ..core.models import HeatmapData

log = get_logger(__name__)


def scott_bandwidth(points: np.ndarray) -> float:
    """Scott's rule: ``n ** (-1/(d+4)) * std``.

    A data-driven default that widens the kernel for small samples (where a
    narrow kernel would show every point as its own island) and narrows it as
    data accumulates.
    """
    n_samples, n_dims = points.shape
    if n_samples < 2:
        return 1.0
    factor = n_samples ** (-1.0 / (n_dims + 4))
    spread = float(np.mean(np.std(points, axis=0))) or 1.0
    return max(0.15, factor * spread)


def compute_grid(
    points: np.ndarray,
    *,
    grid_size: int = 40,
    bandwidth: float = 0.0,
    bounds: tuple[float, float, float, float] | None = None,
    weights: np.ndarray | None = None,
) -> tuple[np.ndarray, list[float], float]:
    """Gaussian KDE on a regular 2D grid over the x/y plane.

    Returns ``(grid, bounds, bandwidth)`` with ``grid`` row-major ``[y][x]``,
    matching what the frontend canvas expects.
    """
    if points.size == 0:
        return np.zeros((grid_size, grid_size), dtype=np.float32), [0, 0, 0, 0], 1.0

    xy = np.asarray(points[:, :2], dtype=np.float32)
    if bounds is None:
        # Pad the extent so kernels near the edge are not clipped, which would
        # otherwise create a false "gap" ring around the map's border.
        margin = 0.08
        x_min, y_min = xy.min(axis=0)
        x_max, y_max = xy.max(axis=0)
        x_pad = max(0.5, (x_max - x_min) * margin)
        y_pad = max(0.5, (y_max - y_min) * margin)
        extent = [
            float(x_min - x_pad), float(x_max + x_pad),
            float(y_min - y_pad), float(y_max + y_pad),
        ]
    else:
        extent = [float(v) for v in bounds]

    h = bandwidth if bandwidth > 0 else scott_bandwidth(xy)
    xs = np.linspace(extent[0], extent[1], grid_size, dtype=np.float32)
    ys = np.linspace(extent[2], extent[3], grid_size, dtype=np.float32)

    # Separable Gaussian: the 2D kernel factorises, so the grid is evaluated as
    # (grid_size x n) @ (n x grid_size) instead of a grid_size^2 x n tensor.
    # That keeps a 40x40 grid over 10k points comfortably in memory.
    dx = (xs[:, None] - xy[None, :, 0]) / h          # (gx, n)
    dy = (ys[:, None] - xy[None, :, 1]) / h          # (gy, n)
    kx = np.exp(-0.5 * dx * dx)
    ky = np.exp(-0.5 * dy * dy)
    if weights is not None:
        kx = kx * np.asarray(weights, dtype=np.float32)[None, :]
    grid = (ky @ kx.T).astype(np.float32)            # (gy, gx)
    grid /= 2.0 * math.pi * h * h * max(1, xy.shape[0])
    return grid, extent, float(h)


def density_at_points(
    points: np.ndarray, query: np.ndarray | None = None, *, bandwidth: float = 0.0
) -> np.ndarray:
    """KDE evaluated at each point (or at ``query`` locations).

    Used for per-paper local density (how crowded is this paper's neighbourhood)
    and to score a newly placed idea against the existing map.
    """
    if points.size == 0:
        return np.zeros(0, dtype=np.float32)
    source = np.asarray(points[:, :3] if points.shape[1] >= 3 else points, dtype=np.float32)
    target = source if query is None else np.asarray(query, dtype=np.float32)
    if target.ndim == 1:
        target = target.reshape(1, -1)
    target = target[:, : source.shape[1]]

    h = bandwidth if bandwidth > 0 else scott_bandwidth(source)
    out = np.zeros(target.shape[0], dtype=np.float32)
    # Chunked to bound peak memory at ~chunk x n floats.
    chunk = max(1, int(4_000_000 / max(1, source.shape[0])))
    for start in range(0, target.shape[0], chunk):
        block = target[start: start + chunk]
        diff = block[:, None, :] - source[None, :, :]
        squared = np.sum(diff * diff, axis=2)
        out[start: start + chunk] = np.exp(-0.5 * squared / (h * h)).sum(axis=1)
    return (out / max(1, source.shape[0])).astype(np.float32)


def build_heatmap(
    points: np.ndarray,
    *,
    grid_size: int = 40,
    bandwidth: float = 0.0,
    keyword_indices: dict[str, list[int]] | None = None,
    max_layers: int = 12,
    z_slices: int = 4,
) -> HeatmapData:
    """Full heatmap payload: base grid, keyword layers, z cross-sections."""
    if points.size == 0:
        return HeatmapData(grid_size=grid_size)

    grid, extent, h = compute_grid(points, grid_size=grid_size, bandwidth=bandwidth)
    max_density = float(grid.max()) or 1.0
    # Normalised to 0..1 so the frontend colour scale needs no knowledge of the
    # absolute density units.
    normalised = (grid / max_density).astype(np.float32)

    layers: dict[str, list[list[float]]] = {}
    if keyword_indices:
        # Layers are only meaningful with enough points to form a shape.
        ranked = sorted(
            ((term, idx) for term, idx in keyword_indices.items() if len(idx) >= 3),
            key=lambda pair: -len(pair[1]),
        )[:max_layers]
        for term, indices in ranked:
            subset = points[np.asarray(indices, dtype=int)]
            layer_grid, _, _ = compute_grid(
                subset, grid_size=grid_size, bandwidth=h,
                bounds=(extent[0], extent[1], extent[2], extent[3]),
            )
            layer_max = float(layer_grid.max()) or 1.0
            layers[term] = np.round(layer_grid / layer_max, 4).tolist()

    slices: list[dict[str, Any]] = []
    if points.shape[1] >= 3 and z_slices > 1:
        z_values = points[:, 2]
        edges = np.linspace(float(z_values.min()), float(z_values.max()), z_slices + 1)
        for index in range(z_slices):
            low, high = float(edges[index]), float(edges[index + 1])
            mask = (z_values >= low) & (
                z_values <= high if index == z_slices - 1 else z_values < high
            )
            count = int(mask.sum())
            if count == 0:
                slices.append({"z_min": low, "z_max": high, "count": 0, "grid": []})
                continue
            slice_grid, _, _ = compute_grid(
                points[mask], grid_size=grid_size, bandwidth=h,
                bounds=(extent[0], extent[1], extent[2], extent[3]),
            )
            slice_max = float(slice_grid.max()) or 1.0
            slices.append({
                "z_min": round(low, 4), "z_max": round(high, 4), "count": count,
                "grid": np.round(slice_grid / slice_max, 4).tolist(),
            })

    return HeatmapData(
        grid_size=grid_size,
        bounds=[round(v, 4) for v in extent],
        grid=np.round(normalised, 4).tolist(),
        max_density=round(max_density, 8),
        layers=layers,
        z_slices=slices,
    )


def year_heatmap(
    points: np.ndarray,
    years: list[int | None],
    *,
    grid_size: int = 40,
    buckets: int = 4,
) -> list[dict[str, Any]]:
    """Density per time bucket - the "how did this field move?" view.

    Comparing early and late buckets is often the clearest way to see a topic
    emerging or a region going quiet, which feeds temporal gap detection.
    """
    valid = [(index, y) for index, y in enumerate(years) if y]
    if len(valid) < 4:
        return []
    year_values = sorted({y for _, y in valid})
    if len(year_values) < 2:
        return []
    edges = np.linspace(year_values[0], year_values[-1] + 1, buckets + 1)
    _, extent, h = compute_grid(points, grid_size=grid_size)

    out: list[dict[str, Any]] = []
    for index in range(buckets):
        low, high = float(edges[index]), float(edges[index + 1])
        indices = [i for i, y in valid if low <= y < high]
        if not indices:
            out.append({"year_from": int(low), "year_to": int(high) - 1,
                        "count": 0, "grid": []})
            continue
        grid, _, _ = compute_grid(
            points[np.asarray(indices, dtype=int)], grid_size=grid_size,
            bandwidth=h, bounds=(extent[0], extent[1], extent[2], extent[3]),
        )
        grid_max = float(grid.max()) or 1.0
        out.append({
            "year_from": int(low), "year_to": int(high) - 1, "count": len(indices),
            "grid": np.round(grid / grid_max, 4).tolist(),
        })
    return out
