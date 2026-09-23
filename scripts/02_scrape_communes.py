"""Step 2 — first-round results by commune, starting from the département pages found in step 1.

About 36,000 commune pages per year. Downloads are slow (several hours per year) but each page is cached in
data/raw/html_cache_communes/, so an interrupted run can be resumed and a re-run reads everything from disk.
A year whose raw file already exists is not scraped again (unless --force).

Outputs
  data/raw/presidential_{2002,2022}_communes.csv            parsed results with scraping diagnostics
  data/raw/presidential_{2002,2022}_communes_failures.csv   pages that could not be read, after one retry
  data/processed/presidential_communes_2002_2022.csv        both years, one row per year × commune × candidate

Paris, Lyon and Marseille are whole communes; their arrondissement pages are only read to check that they add up.

Usage: python scripts/02_scrape_communes.py [--force] [--offline] [--limit N]
  --limit N   test run on the first N départements of each year; nothing is saved
"""
import argparse

import numpy as np
import pandas as pd

from svgeo import communes as com
from svgeo.config import (COMMUNE_FAILURES, COMMUNE_RAW, COMMUNE_RESULTS, EXPECTED_K, FIRST_ROUND_ONLY, PLM,
                          YEARS)
from svgeo.utils import norm_text, read_csv, report, show
from svgeo.web import commune_pages


def scrape_year(year, limit=None):
    deps = com.load_department_urls(year)
    commune_urls, arr_urls, failures = com.discover_all_communes(year, deps, limit=limit, verbose=False)
    commune_urls, arr_urls, failures = com.retry_discovery(year, deps, commune_urls, arr_urls, failures)
    dup = commune_urls[commune_urls.duplicated(["commune_code"], keep=False)]
    report(f"{year}: no duplicated commune code among the URLs", dup.empty, dup)
    results, failures = com.scrape_communes(year, commune_urls, failures)
    results, failures = com.retry_commune_failures(year, commune_urls, results, failures)
    print(f"{year}: {results['commune_code'].nunique()} communes parsed | failures by level:",
          failures.groupby("level").size().to_dict())
    return results, failures


def validate(results, failures):
    communes = results.drop_duplicates(["year", "commune_code"])
    print("\nCommunes scraped per year:")
    show(communes.groupby("year").agg(communes=("commune_code", "nunique"), departements=("department_code", "nunique")))
    print("Failures per year:")
    show(failures.groupby(["year", "level"]).size())

    print("\n== Commune codes ==")
    report("no missing commune code", results["commune_code"].notna().all(), results[results["commune_code"].isna()])
    report("commune codes have 5 characters", communes["commune_code"].str.len().eq(5).all(),
           communes.loc[communes["commune_code"].str.len().ne(5), ["year", "commune_code", "commune_code_source"]])
    dep_of_code = communes["commune_code"].map(com.department_from_commune)
    report("commune code consistent with its département", dep_of_code.eq(communes["department_code"]).all(),
           communes.loc[dep_of_code.ne(communes["department_code"]),
                        ["year", "department_code", "commune_code", "commune_code_source", "commune_name"]])
    dup = results.duplicated(["year", "commune_code", "candidate"], keep=False)
    report("no duplicated year × commune × candidate rows", not dup.any(), results[dup])

    print("\n== Candidates, votes, turnout ==")
    n_cand = results.groupby(["year", "commune_code"])["candidate_clean"].nunique().rename("n").reset_index()
    n_cand["expected"] = n_cand["year"].map(EXPECTED_K)
    report("every commune lists all K candidates", n_cand["n"].eq(n_cand["expected"]).all(),
           n_cand[n_cand["n"] != n_cand["expected"]])
    by_com = results.groupby(["year", "commune_code"]).agg(
        sum_votes=("votes", "sum"), expressed=("expressed", "first"), sum_share=("vote_share", "sum"),
        voters=("voters", "first"), blank_null=("blank_null", "first")).reset_index()
    bad = by_com[by_com["sum_votes"] != by_com["expressed"]]
    report(f"candidate votes sum to expressed ({len(bad)} mismatches)", bad.empty, bad)
    bad = by_com[(by_com["expressed"] > 0) & ((by_com["sum_share"] - 100).abs() > 0.2)]
    report(f"published vote shares sum to ~100% (±0.2) ({len(bad)} exceptions)", bad.empty, bad)
    bad = by_com[by_com["voters"] != by_com["expressed"] + by_com["blank_null"]]
    report(f"voters == expressed + blank/null ({len(bad)} exceptions)", bad.empty, bad)
    bad = by_com[by_com["expressed"] <= 0]
    report(f"no commune with 0 expressed votes ({len(bad)} exceptions: results annulled, all ballots blank/null)",
           bad.empty, bad)

    print("\n== First round only ==")
    report("no commune with only 2 candidates (round 2)", (n_cand["n"] > 2).all(), n_cand[n_cand["n"] <= 2])
    for year, names in FIRST_ROUND_ONLY.items():
        sub = results[results["year"] == year]
        words = (sub.assign(w=sub["candidate"].map(lambda c: set(norm_text(c).upper().split())))
                    .groupby("commune_code")["w"].apply(lambda ws: set().union(*ws)))
        for name in names:
            lacking = words[~words.map(lambda s: name in s)]
            report(f"{year}: first-round-only candidate {name} listed in every commune", lacking.empty, lacking)
    report("turnout table consistent with the candidate table", results["turnout_matches_sum"].all(),
           communes.loc[~communes["turnout_matches_sum"].astype(bool), ["year", "commune_code", "source_url"]])

    print("\nCommunes by number of expressed votes:")
    show(pd.crosstab(pd.cut(by_com["expressed"], [0, 50, 100, 500, 1000, 10_000, np.inf], right=False),
                     by_com["year"]))

    print("\n== Paris, Lyon, Marseille (whole communes; arrondissements must add up) ==")
    show(communes.loc[communes["commune_code"].isin(PLM), ["year", "commune_code", "commune_name", "expressed"]])
    for year in YEARS:
        present = set(communes.loc[communes["year"] == year, "commune_code"])
        report(f"{year}: whole-commune results for Paris, Lyon, Marseille", set(PLM) <= present,
               pd.Series(sorted(set(PLM) - present), dtype=str))
        cmp = com.plm_check(results, year)
        if cmp is not None:
            report(f"{year}: arrondissements add up to the whole communes", cmp["difference"].fillna(1).eq(0).all(),
                   cmp[cmp["difference"] != 0])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="scrape again even if the raw files exist")
    parser.add_argument("--offline", action="store_true", help="use cached pages only, never download")
    parser.add_argument("--limit", type=int, help="test on the first N départements of each year; nothing is saved")
    args = parser.parse_args()
    commune_pages.offline = commune_pages.offline or args.offline

    if args.limit:
        for year in YEARS:
            results, failures = scrape_year(year, limit=args.limit)
            show(results.head(EXPECTED_K[year]))
        return

    scraped = False
    for year in YEARS:
        if COMMUNE_RAW[year].exists() and not args.force:
            print(f"{year}: {COMMUNE_RAW[year].name} exists — not scraped again")
            continue
        results, failures = scrape_year(year)
        COMMUNE_RAW[year].parent.mkdir(parents=True, exist_ok=True)
        results.to_csv(COMMUNE_RAW[year], index=False, encoding="utf-8")
        failures.to_csv(COMMUNE_FAILURES[year], index=False, encoding="utf-8")
        scraped = True

    if COMMUNE_RESULTS.exists() and not (scraped or args.force):
        print(f"{COMMUNE_RESULTS.name} exists — nothing to do (use --force to rebuild)")
        return

    results = pd.concat([read_csv(COMMUNE_RAW[y]) for y in YEARS], ignore_index=True)
    failures = pd.concat([read_csv(COMMUNE_FAILURES[y]) for y in YEARS], ignore_index=True)
    results["candidate_clean"] = (results["candidate"].str.replace(r"^(M\.|Mme\.?|Mlle\.?)\s+", "", regex=True)
                                  .str.strip())
    validate(results, failures)
    COMMUNE_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(COMMUNE_RESULTS, index=False, encoding="utf-8")
    print(f"\nSaved {len(results)} rows to {COMMUNE_RESULTS}")


if __name__ == "__main__":
    main()
