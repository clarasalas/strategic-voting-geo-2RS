import numpy as np
import pandas as pd

from svgeo.indices import compute_cenp, compute_cliff_metrics, compute_enp, compute_indices


def test_cenp_bounds():
    assert np.isclose(compute_enp(np.full(4, 0.25)), 4)
    assert np.isclose(compute_cenp(np.full(4, 0.25), 4), 0)
    assert np.isclose(compute_cenp(np.array([1.0, 0, 0, 0]), 4), 1)


def test_cliff_between_second_and_third():
    cliff = compute_cliff_metrics(np.array([0.40, 0.35, 0.15, 0.10]))  # gaps 0.05, 0.20, 0.05
    assert cliff["cliff_location"] == 2
    assert np.isclose(cliff["cliff_magnitude"], 0.20)
    assert np.isclose(cliff["cliff_ratio"], 0.20 / (0.20 + 0.05))


def test_compute_indices_fills_missing_candidates_and_skips_zero_votes():
    results = pd.DataFrame({
        "year": 2002, "unit": ["a", "a", "b", "c", "c"],
        "candidate_clean": ["X", "Y", "X", "X", "Y"], "votes": [3, 1, 5, 0, 0],
    })
    out = compute_indices(results, "unit", {2002: 2}).set_index("unit")
    assert list(out.index) == ["a", "b"]  # 'c' has no votes
    assert np.isclose(out.loc["b", "CENP"], 1)  # Y absent from 'b' -> share 0
    assert out.loc["a", "winner"] == "X"
