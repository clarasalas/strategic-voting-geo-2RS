"""Spatial autocorrelation of département-level values and regression residuals (numpy only, no PySAL).

| tool              | what it answers                                                                               |
|-------------------|-----------------------------------------------------------------------------------------------|
| Moran's I         | do neighbouring départements have similar values? > 0 clustered, ≈ −1/(n−1) none, < 0 dispersed |
| residual Moran    | same question for OLS residuals, with the Cliff–Ord test that accounts for the estimated slope |
| local Moran (LISA)| which départements sit in a cluster: HH / LL (similar to their neighbours), HL / LH (outliers) |
| Conley SE         | standard error of a slope allowing correlation between départements closer than a cutoff       |

Neighbours are defined by a row-standardised weight matrix W (each row sums to 1): shared border (`contiguity_weights`)
or the k nearest centroids (`knn_weights`). Results should not depend on this choice, so several are compared.
"""
import numpy as np
import pandas as pd
from scipy import stats

from svgeo.config import DEPARTMENT_BOUNDARIES

LAMBERT_93 = 2154


def load_department_geometry(codes, path=DEPARTMENT_BOUNDARIES):
    """One (multi)polygon per département code, in the order of `codes`, Lambert-93 (metres).

    The GeoJSON must have a `code` property ('01', '2A', …). Invalid simplified polygons are repaired before
    features sharing a code are dissolved.
    """
    import geopandas as gpd  # only needed here

    geo = gpd.read_file(path)
    geo["department_code"] = geo["code"].astype(str).str.zfill(2)
    geo = geo[geo["department_code"].isin(codes)].copy()
    geo["geometry"] = geo.geometry.make_valid()
    geo = geo.dissolve("department_code").reset_index().to_crs(LAMBERT_93)
    missing = set(codes) - set(geo["department_code"])
    assert not missing, f"no geometry for {sorted(missing)}"
    return geo.set_index("department_code").loc[list(codes)].reset_index()


def row_standardise(W):
    return W / W.sum(axis=1, keepdims=True)


def contiguity_weights(geo, tolerance_m=200):
    """Neighbours = départements sharing a border. The small buffer closes gaps left by simplified boundaries."""
    grown = geo.geometry.buffer(tolerance_m)
    W = np.array([grown.intersects(g).values for g in grown], dtype=float)
    np.fill_diagonal(W, 0)
    assert (W.sum(axis=1) > 0).all(), "a département has no neighbour"
    return row_standardise(W)


def centroids_km(geo):
    c = geo.geometry.centroid
    return np.c_[c.x, c.y] / 1000


def knn_weights(geo, k):
    """Neighbours = the k départements with the nearest centroids."""
    xy = centroids_km(geo)
    d = np.linalg.norm(xy[:, None] - xy[None], axis=2)
    np.fill_diagonal(d, np.inf)
    W = np.zeros_like(d)
    for i, row in enumerate(d):
        W[i, np.argsort(row)[:k]] = 1
    return row_standardise(W)


def moran_i(values, W):
    """Global Moran's I = (n / S0) · z'Wz / z'z, z = deviations from the mean."""
    z = np.asarray(values, dtype=float) - np.mean(values)
    return (len(z) / W.sum()) * (z @ W @ z) / (z @ z)


def moran_test(values, W, n_perm=9999, seed=0):
    """Moran's I with a two-sided permutation p-value (values reshuffled across départements)."""
    rng = np.random.default_rng(seed)
    values = np.asarray(values, dtype=float)
    I = moran_i(values, W)
    sims = np.array([moran_i(rng.permutation(values), W) for _ in range(n_perm)])
    p = (1 + (np.abs(sims - sims.mean()) >= abs(I - sims.mean())).sum()) / (n_perm + 1)
    return {"I": I, "E[I]": -1 / (len(values) - 1), "p_perm": p}


def moran_residuals_test(X, residuals, W):
    """Moran's I of OLS residuals with the Cliff–Ord normal approximation (X includes the constant)."""
    X, e = np.asarray(X, dtype=float), np.asarray(residuals, dtype=float)
    n, k = X.shape
    M = np.eye(n) - X @ np.linalg.solve(X.T @ X, X.T)
    MW = M @ W
    scale = n / W.sum()
    I = scale * (e @ W @ e) / (e @ e)
    expected = scale * np.trace(MW) / (n - k)
    variance = (scale ** 2 * (np.trace(MW @ MW.T) + np.trace(MW @ MW) + np.trace(MW) ** 2)
                / ((n - k) * (n - k + 2)) - expected ** 2)
    z = (I - expected) / np.sqrt(variance)
    return {"I": I, "E[I]": expected, "z": z, "p_analytic": 2 * stats.norm.sf(abs(z))}


def local_moran(values, W, n_perm=9999, seed=0):
    """Local Moran's I_i = z_i · (Wz)_i with conditional permutation p-values (unit i fixed, others reshuffled).

    `quadrant`: HH / LL = high (low) value among high (low) neighbours, HL / LH = spatial outliers.
    The p-values are not corrected for testing every département: read them as descriptive.
    """
    rng = np.random.default_rng(seed)
    v = np.asarray(values, dtype=float)
    z = (v - v.mean()) / v.std()
    lag = W @ z
    local = z * lag
    p = np.empty(len(z))
    for i in range(len(z)):
        others, w = np.delete(z, i), np.delete(W[i], i)
        sims = z[i] * np.array([w @ rng.permutation(others) for _ in range(n_perm)])
        p[i] = (1 + (np.abs(sims) >= abs(local[i])).sum()) / (n_perm + 1)
    quadrant = np.select([(z > 0) & (lag > 0), (z < 0) & (lag < 0), z > 0], ["HH", "LL", "HL"], "LH")
    return pd.DataFrame({"z": z, "spatial_lag": lag, "local_I": local, "quadrant": quadrant, "p_perm": p})


def conley_se(X, residuals, coords_km, cutoff_km):
    """OLS standard errors allowing correlation between units closer than `cutoff_km` (uniform kernel).

    With a cutoff of 0 only a unit's own residual enters, which gives the HC0 standard errors.
    """
    X, e = np.asarray(X, dtype=float), np.asarray(residuals, dtype=float)
    d = np.linalg.norm(coords_km[:, None] - coords_km[None], axis=2)
    kernel = (d <= cutoff_km).astype(float)
    bread = np.linalg.inv(X.T @ X)
    meat = X.T @ (kernel * np.outer(e, e)) @ X
    return np.sqrt(np.diag(bread @ meat @ bread))
