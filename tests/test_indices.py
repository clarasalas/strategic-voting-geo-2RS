import numpy as np
import pandas as pd

from svgeo.indices import compute_cenp, compute_enp, compute_hhi, compute_hhi_corrected, compute_indices


def test_cenp_bounds():
    assert np.isclose(compute_enp(np.full(4, 0.25)), 4)
    assert np.isclose(compute_cenp(np.full(4, 0.25), 4), 0)
    assert np.isclose(compute_cenp(np.array([1.0, 0, 0, 0]), 4), 1)


def test_hhi_uniform_and_complete_concentration():
    for K in (2, 4, 12, 16):
        assert np.isclose(compute_hhi(np.full(K, 1 / K)), 1 / K)
    assert np.isclose(compute_hhi(np.array([1.0, 0, 0, 0])), 1)


def test_hhi_unchanged_by_zero_share_candidate_and_enp_is_inverse():
    shares = np.array([0.40, 0.35, 0.15, 0.10])
    assert np.isclose(compute_hhi(np.append(shares, 0.0)), compute_hhi(shares))
    for s in (shares, np.full(5, 0.2), np.array([1.0, 0.0])):
        assert np.isclose(compute_enp(s), 1 / compute_hhi(s))


def test_hhi_corrected_matches_both_formulas():
    votes = np.array([50, 30, 15, 4, 1, 0])
    n = votes.sum()
    hhi = compute_hhi(votes / n)
    expected = np.sum(votes * (votes - 1)) / (n * (n - 1))
    assert np.isclose(compute_hhi_corrected(votes), expected)
    assert np.isclose(compute_hhi_corrected(votes), (n * hhi - 1) / (n - 1))
    assert np.isclose(compute_hhi_corrected(np.array([7, 0, 0])), 1)
    assert np.isclose(compute_hhi_corrected(np.array([1, 1, 1])), 0)


def test_hhi_corrected_small_electorates():
    assert np.isnan(compute_hhi_corrected(np.array([0, 0, 0])))
    assert np.isnan(compute_hhi_corrected(np.array([1, 0, 0])))
    assert np.isclose(compute_hhi_corrected(np.array([2, 0])), 1)


def test_compute_indices_fills_missing_candidates_and_skips_zero_votes():
    results = pd.DataFrame({
        "year": 2002, "unit": ["a", "a", "b", "c", "c"],
        "candidate_clean": ["X", "Y", "X", "X", "Y"], "votes": [3, 1, 5, 0, 0],
    })
    out = compute_indices(results, "unit", {2002: 2}).set_index("unit")
    assert list(out.index) == ["a", "b"]  # 'c' has no votes
    assert np.isclose(out.loc["b", "CENP"], 1)  # Y absent from 'b' -> share 0
    assert out.loc["a", "winner"] == "X"
    assert np.isclose(out.loc["a", "HHI"], 0.75 ** 2 + 0.25 ** 2)
    assert np.isclose(out.loc["a", "ENP"], 1 / out.loc["a", "HHI"])
    assert np.isclose(out.loc["a", "HHI_corrected"], (3 * 2 + 0) / (4 * 3))
