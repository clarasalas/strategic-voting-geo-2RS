"""Step 3 — concentration indices (HHI, corrected HHI, ENP, CENP) by département and by commune.

Shares are recomputed from the counts, δ_j = votes_j / Σ_k votes_k, with K = number of candidates nationally
(16 in 2002, 12 in 2022). Definitions: svgeo/indices.py. HHI and CENP describe the observed result only (there is no
sincere counterfactual here), so they are not a coordination *gain* and do not identify strategic voting.

Inputs   data/processed/presidential_{departments,communes}_2002_2022.csv (steps 1 and 2)
Outputs  data/processed/coordination_indices_departements.csv   one row per year × département
         data/processed/coordination_indices_communes.csv       one row per year × commune

Usage: python scripts/03_coordination_indices.py [--force] [--level departements|communes]
"""
import argparse

import numpy as np
import pandas as pd

from svgeo.config import (COMMUNE_INDICES, COMMUNE_RESULTS, DEPARTMENT_INDICES, DEPARTMENT_RESULTS, EXPECTED_K, PLM)
from svgeo.indices import compute_indices
from svgeo.utils import read_csv, report, show

INDEX_COLUMNS = ["HHI", "HHI_corrected", "ENP", "CENP"]
# May be missing for a unit with votes: corrected HHI when n <= 1
OPTIONAL_INDEX_COLUMNS = ["HHI_corrected"]


def check_k(results):
    K_by_year = results.groupby("year")["candidate_clean"].nunique().to_dict()
    assert K_by_year == EXPECTED_K, f"number of candidates {K_by_year} differs from the expected {EXPECTED_K}"
    return K_by_year


def validate(indices, unit):
    dup = indices.duplicated(["year", unit], keep=False)
    report(f"no duplicated year × {unit} rows", not dup.any(), indices[dup])
    ok = indices["CENP"].notna()
    d = indices[ok]
    required = [c for c in INDEX_COLUMNS if c not in OPTIONAL_INDEX_COLUMNS]
    report(f"indices computed for every {unit} with votes ({(~ok).sum()} without)", not d[required].isna().any().any())
    report("K as expected", d["K"].eq(d["year"].map(EXPECTED_K)).all())
    report("HHI within [1/K, 1]", (d["HHI"].ge(1 / d["K"] - 1e-12) & d["HHI"].le(1 + 1e-12)).all(),
           d.loc[~d["HHI"].between(0, 1), ["year", unit, "HHI"]])
    report("ENP = 1 / HHI", np.allclose(d["ENP"], 1 / d["HHI"]))
    small = d["expressed"] <= 1
    hc = d.loc[~small, "HHI_corrected"]
    report(f"corrected HHI within [0, 1] where expressed > 1, missing exactly where expressed <= 1 ({small.sum()} units)",
           hc.notna().all() and hc.between(-1e-12, 1 + 1e-12).all() and d.loc[small, "HHI_corrected"].isna().all(),
           d.loc[~small & ~d["HHI_corrected"].between(0, 1), ["year", unit, "expressed", "HHI_corrected"]])
    report("corrected HHI <= HHI (finite-electorate noise only raises observed concentration)",
           (hc <= d.loc[~small, "HHI"] + 1e-12).all())
    report("CENP within [0, 1]", d["CENP"].between(0, 1).all(), d.loc[~d["CENP"].between(0, 1), ["year", unit, "CENP"]])
    report("ENP within [1, K]", (d["ENP"].ge(1 - 1e-12) & d["ENP"].le(d["K"] + 1e-12)).all())
    print("Summary by year:")
    show(d.groupby("year")[INDEX_COLUMNS].agg(["min", "max", "mean"]).T)


def departements():
    results = read_csv(DEPARTMENT_RESULTS)
    # In 2022 Saint-Martin/Saint-Barthélemy was found twice: as 'ZX' (link label) and as '977' (URL, no name).
    # Rows without a département name are such duplicates.
    results = results[results["dep_name"].notna()].copy()

    # On the 2022 pages the turnout figures do not always match the first-round candidate votes: registered /
    # voters are only reported where `exprimes` equals the sum of the candidate votes
    by_dep = results.groupby(["year", "dep_code"]).agg(sum_votes=("votes", "sum"), exprimes=("exprimes", "first"))
    by_dep["turnout_consistent"] = by_dep["sum_votes"] == by_dep["exprimes"]
    print("Départements where scraped 'exprimes' == sum of candidate votes:")
    show(by_dep.groupby("year")["turnout_consistent"].agg(["sum", "size"]))

    indices = compute_indices(results, "dep_code", check_k(results))
    context = (results.groupby(["year", "dep_code"])
                      .agg(department_name=("dep_name", "first"), department_type=("dep_type", "first"),
                           registered=("inscrits", "first"), voters=("votants", "first"), expressed=("votes", "sum"))
                      .join(by_dep["turnout_consistent"]).reset_index())
    context.loc[~context["turnout_consistent"], ["registered", "voters"]] = np.nan
    out = (indices.merge(context, on=["year", "dep_code"], how="left")
                  .rename(columns={"dep_code": "department_code"}))
    out = out[["year", "department_code", "department_name", "department_type", "K", *INDEX_COLUMNS,
               "winner", "winner_vote_share", "registered", "voters", "expressed", "turnout_consistent"]]
    out = out.sort_values(["year", "department_code"]).reset_index(drop=True)
    validate(out, "department_code")
    report("Corsica split into 2A and 2B in both years",
           all({"2A", "2B"} <= set(out.loc[out["year"] == y, "department_code"]) for y in EXPECTED_K))
    return out


def communes():
    results = read_csv(COMMUNE_RESULTS)
    indices = compute_indices(results, "commune_code", check_k(results))
    info = results.drop_duplicates(["year", "commune_code"])[
        ["year", "department_code", "department_name", "commune_code", "commune_name",
         "registered", "abstentions", "voters", "blank_null", "expressed", "turnout_matches_sum"]]
    out = info.merge(indices, on=["year", "commune_code"], how="left", validate="one_to_one")
    out["plm_source"] = np.where(out["commune_code"].isin(PLM), "whole-commune page", "")
    out = out[["year", "department_code", "department_name", "commune_code", "commune_name", "K", *INDEX_COLUMNS,
               "winner", "winner_vote_share", "registered", "abstentions", "voters", "blank_null", "expressed",
               "turnout_matches_sum", "plm_source"]]
    out = out.sort_values(["year", "commune_code"]).reset_index(drop=True)
    validate(out, "commune_code")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild even if the outputs exist")
    parser.add_argument("--level", choices=["departements", "communes"], help="only one level (default: both)")
    args = parser.parse_args()

    for level, build, path in [("departements", departements, DEPARTMENT_INDICES),
                               ("communes", communes, COMMUNE_INDICES)]:
        if args.level not in (None, level):
            continue
        if path.exists() and not args.force:
            print(f"{path.name} exists — skipped (use --force to rebuild)")
            continue
        print(f"\n===== {level} =====")
        out = build()
        out.to_csv(path, index=False, encoding="utf-8")
        print(f"Saved {len(out)} rows to {path}")


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    main()
