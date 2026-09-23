"""Coordination measures of a first-round result, from the candidates' vote shares δ (proportions).

| measure         | definition                                                                        |
|-----------------|-----------------------------------------------------------------------------------|
| ENP             | 1 / Σ δ_j²                                                                        |
| CENP (main)     | (K − ENP) / (K − 1): 0 = uniform over the K candidates, 1 = all votes on one      |
| cliff magnitude | d* = max_k g_k, g_k = δ_(k) − δ_(k+1), shares sorted in decreasing order         |
| cliff location  | argmax_k g_k, 1-based: 2 = largest drop between the 2nd and 3rd candidates        |
| cliff ratio     | d* / (d* + mean of the other gaps), in [0.5, 1]                                   |

K = number of candidates nationally (16 in 2002, 12 in 2022); a candidate with no vote in a unit enters with δ = 0.
"""
import numpy as np
import pandas as pd


def compute_enp(shares):
    """Effective number of parties: 1 / sum(delta_j^2)."""
    shares = np.asarray(shares, dtype=float)
    return 1.0 / np.sum(shares ** 2)


def compute_cenp(shares, K):
    """(K - ENP) / (K - 1). `shares` must contain all K candidates of the election (zeros included)."""
    shares = np.asarray(shares, dtype=float)
    assert len(shares) == K, f"expected {K} shares, got {len(shares)}"
    return (K - compute_enp(shares)) / (K - 1)


def compute_cliff_metrics(shares):
    """Largest drop between consecutive ranked shares; if several gaps are equal the highest-ranked one is taken.

    cliff_ratio is NaN if all gaps are 0.
    """
    ranked = np.sort(np.asarray(shares, dtype=float))[::-1]
    gaps = ranked[:-1] - ranked[1:]
    k = int(np.argmax(gaps))
    d_star = gaps[k]
    other_gaps = np.delete(gaps, k)
    denom = d_star + (other_gaps.mean() if len(other_gaps) else 0.0)
    return {"cliff_magnitude": d_star, "cliff_location": k + 1,
            "cliff_ratio": d_star / denom if denom > 0 else np.nan}


def compute_indices(results, unit, K_by_year):
    """One row per year × unit (`unit` = column with the département or commune code).

    `results`: long format, one row per year × unit × candidate with `candidate_clean` and `votes`.
    Units with 0 expressed votes have no shares: they are listed and get no row.
    """
    rows = []
    for year, K in K_by_year.items():
        votes = results[results["year"] == year].pivot_table(
            index=unit, columns="candidate_clean", values="votes", aggfunc="sum", fill_value=0)
        assert votes.shape[1] == K, f"{year}: {votes.shape[1]} candidates in the data, expected {K}"
        totals = votes.sum(axis=1)
        zero = totals[totals == 0].index
        if len(zero):
            print(f"{year}: {len(zero)} units with 0 expressed votes — no indices:", list(zero))
        shares = votes.loc[totals > 0].div(totals[totals > 0], axis=0)
        for code, s in shares.iterrows():
            rows.append({"year": year, unit: code, "K": K,
                         "ENP": compute_enp(s.values), "CENP": compute_cenp(s.values, K),
                         **compute_cliff_metrics(s.values),
                         "winner": s.idxmax(), "winner_vote_share": s.max()})
    return pd.DataFrame(rows)
