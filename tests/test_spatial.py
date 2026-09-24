import numpy as np

from svgeo.spatial import conley_se, local_moran, moran_i, moran_residuals_test, moran_test, row_standardise


def line_weights(n):
    """Units on a line, each neighbouring the previous and the next one."""
    W = np.zeros((n, n))
    for i in range(n - 1):
        W[i, i + 1] = W[i + 1, i] = 1
    return row_standardise(W)


def test_moran_alternating_pattern_is_minus_one():
    W = line_weights(10)
    assert np.isclose(moran_i(np.tile([1.0, -1.0], 5), W), -1)


def test_moran_clustered_pattern_is_positive_and_significant():
    W = line_weights(40)
    values = np.r_[np.ones(20), -np.ones(20)]
    out = moran_test(values, W, n_perm=999)
    assert out["I"] > 0.8
    assert out["p_perm"] < 0.01
    assert np.isclose(out["E[I]"], -1 / 39)


def test_residual_test_agrees_with_moran_i():
    rng = np.random.default_rng(1)
    W = line_weights(30)
    X = np.c_[np.ones(30), rng.normal(size=30)]
    e = rng.normal(size=30)
    e = e - X @ np.linalg.lstsq(X, e, rcond=None)[0]  # genuine OLS residuals
    out = moran_residuals_test(X, e, W)
    assert np.isclose(out["I"], moran_i(e, W))  # residuals have mean 0
    assert out["E[I]"] < 0


def test_local_moran_quadrants():
    W = line_weights(6)
    lisa = local_moran(np.array([3.0, 3, 3, -3, -3, -3]), W, n_perm=99)
    assert list(lisa["quadrant"][:2]) == ["HH", "HH"]
    assert list(lisa["quadrant"][-2:]) == ["LL", "LL"]


def test_conley_with_zero_cutoff_is_hc0():
    rng = np.random.default_rng(2)
    X = np.c_[np.ones(50), rng.normal(size=50)]
    e = rng.normal(size=50)
    coords = rng.uniform(0, 1000, size=(50, 2))
    bread = np.linalg.inv(X.T @ X)
    hc0 = np.sqrt(np.diag(bread @ (X.T * e ** 2) @ X @ bread))
    assert np.allclose(conley_se(X, e, coords, 0), hc0)
