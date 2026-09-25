"""Concentration measures of a first-round result, from the candidates' vote shares δ (proportions).

| measure         | definition                                                                               |
|-----------------|------------------------------------------------------------------------------------------|
| HHI (main)      | Σ δ_j²: observed vote concentration, from 1/K (uniform) to 1 (all votes on one)          |
| HHI_corrected   | (n·HHI − 1) / (n − 1) = Σ n_j(n_j − 1) / (n(n − 1)), n = expressed votes; NaN if n ≤ 1   |
| ENP             | 1 / HHI, the effective number of candidates                                              |
| CENP            | (K − ENP) / (K − 1): 0 = uniform over the K candidates, 1 = all votes on one             |

K = number of candidates nationally (16 in 2002, 12 in 2022); a candidate with no vote in a unit enters with δ = 0.

HHI is the measure used to compare 2002 and 2022: an unchanged vote vector has an unchanged HHI whatever the number
of candidates on the ballot, and adding a candidate with no vote leaves it unchanged. CENP depends directly on K (the
same ENP gives a different CENP with 16 or 12 candidates), so it is kept as a supplementary normalised measure, linked
to the ABM, and for comparisons within one election (for a given K it orders units exactly as HHI does).

HHI_corrected removes the finite-electorate part of HHI: if n votes are drawn from shares p, E[HHI] = Σp² + (1 − Σp²)/n,
whereas E[HHI_corrected] = Σp². It estimates the underlying concentration; HHI describes the realised result.
None of these measures identifies strategic voting on its own.
"""
import numpy as np
import pandas as pd


def compute_hhi(shares):
    """Herfindahl–Hirschman index: sum(delta_j^2)."""
    shares = np.asarray(shares, dtype=float)
    return np.sum(shares ** 2)


def compute_hhi_corrected(votes):
    """Finite-electorate corrected HHI from vote counts: sum n_j(n_j - 1) / (n(n - 1)); NaN if n <= 1."""
    votes = np.asarray(votes, dtype=float)
    n = votes.sum()
    if n <= 1:
        return np.nan
    return np.sum(votes * (votes - 1)) / (n * (n - 1))


def compute_enp(shares):
    """Effective number of parties: 1 / HHI."""
    return 1.0 / compute_hhi(shares)


def compute_cenp(shares, K):
    """(K - ENP) / (K - 1). `shares` must contain all K candidates of the election (zeros included)."""
    shares = np.asarray(shares, dtype=float)
    assert len(shares) == K, f"expected {K} shares, got {len(shares)}"
    return (K - compute_enp(shares)) / (K - 1)


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
                         "HHI": compute_hhi(s.values), "HHI_corrected": compute_hhi_corrected(votes.loc[code].values),
                         "ENP": compute_enp(s.values), "CENP": compute_cenp(s.values, K),
                         "winner": s.idxmax(), "winner_vote_share": s.max()})
    return pd.DataFrame(rows)
