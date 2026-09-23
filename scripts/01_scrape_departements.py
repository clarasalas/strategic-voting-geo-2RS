"""Step 1 — first-round results of the 2002 and 2022 presidential elections by département.

Source: archives-resultats-elections.interieur.gouv.fr (pages cached in data/raw/html_cache/).

Outputs
  data/raw/presidential_{2002,2022}_department_urls.csv   département pages found by crawling
  data/raw/presidential_{2002,2022}_departments.csv       parsed results with scraping diagnostics
  data/processed/presidential_departments_2002_2022.csv   both years, one row per year × département × candidate
  data/raw/presidential_departments_failures.csv          only if some pages failed

Usage: python scripts/01_scrape_departements.py [--force] [--offline] [--limit N]
"""
import argparse
import re

import pandas as pd

from svgeo import departements as dep
from svgeo.config import (DEPARTMENT_FAILURES, DEPARTMENT_RAW, DEPARTMENT_RESULTS, DEPARTMENT_URLS, EXPECTED_K,
                          FIRST_ROUND_ONLY, YEARS)
from svgeo.utils import norm_text, report, show
from svgeo.web import department_pages

OUTPUTS = [*DEPARTMENT_URLS.values(), *DEPARTMENT_RAW.values(), DEPARTMENT_RESULTS]


def validate(results, dep_urls, failures):
    deps = results.drop_duplicates(["year", "dep_code"])  # one row per year × département

    print("\n== Départements ==")
    for year in YEARS:
        codes = deps.loc[deps["year"] == year, "dep_code"]
        report(f"{year}: département codes are unique", not codes.duplicated().any(), codes[codes.duplicated()])
        missing = sorted(dep.EXPECTED_DEPS[year] - set(codes))
        report(f"{year}: no missing département — missing: {missing}", not missing)
        print(f"   other territories included: {sorted(set(codes) - dep.EXPECTED_DEPS[year])}")
    dup = results.duplicated(["year", "dep_code", "candidate"], keep=False)
    report("no duplicated year × département × candidate rows", not dup.any(), results[dup])
    report("no scraping failures", failures.empty, failures)

    print("\n== Candidates ==")
    for year in YEARS:
        n_by_dep = results[results["year"] == year].groupby("dep_code")["candidate"].nunique()
        report(f"{year}: every département has {EXPECTED_K[year]} candidates",
               (n_by_dep == EXPECTED_K[year]).all(), n_by_dep[n_by_dep != EXPECTED_K[year]])

    print("\n== Votes, shares, turnout ==")
    by_dep = results.groupby(["year", "dep_code"]).agg(
        exprimes=("exprimes", "first"), sum_votes=("votes", "sum"),
        sum_share=("vote_share", "sum"), sum_share_calc=("vote_share_calc", "sum")).reset_index()
    bad = by_dep[by_dep["sum_votes"] != by_dep["exprimes"]]
    report(f"sum of candidate votes == Exprimés ({len(bad)} exceptions)", bad.empty, bad)
    bad = by_dep[(by_dep["sum_share"] - 100).abs() > 0.2]  # published shares are rounded to 2 decimals
    report("published vote shares sum to ~100 % (±0.2)", bad.empty, bad)
    acc = deps.assign(check1=deps["inscrits"] - deps["abstentions"], check2=deps["exprimes"] + deps["blancs_nuls"])
    report("Votants == Inscrits − Abstentions", (acc["votants"] == acc["check1"]).all(),
           acc.loc[acc["votants"] != acc["check1"], ["year", "dep_code", "inscrits", "abstentions", "votants"]])
    report("Votants == Exprimés + Blancs/nuls", (acc["votants"] == acc["check2"]).all(),
           acc.loc[acc["votants"] != acc["check2"], ["year", "dep_code", "votants", "exprimes", "blancs_nuls"]])

    print("\n== First round only ==")
    n_by_dep = results.groupby(["year", "dep_code"])["candidate"].nunique()
    report("no département with only 2 candidates (round 2)", (n_by_dep > 2).all(), n_by_dep[n_by_dep <= 2])
    for year, names in FIRST_ROUND_ONLY.items():
        sub = results[results["year"] == year]
        words = (sub.assign(w=sub["candidate"].map(lambda c: set(norm_text(c).upper().split())))
                    .groupby("dep_code")["w"].apply(lambda ws: set().union(*ws)))
        for name in names:
            lacking = words[~words.map(lambda s: name in s)]
            report(f"{year}: first-round-only candidate {name} present in every département", lacking.empty, lacking)
        sets = sub.groupby("dep_code")["candidate_clean"].apply(frozenset)
        report(f"{year}: same candidate list in every département", sets.nunique() == 1,
               sets.value_counts().to_frame("n_departements"))
    report("no table labelled '2nd tour' used", not deps["table_round_label"].eq(2).any(),
           deps.loc[deps["table_round_label"].eq(2), ["year", "dep_code", "source_url"]])
    report("turnout table matches candidate table in every département", deps["turnout_matches_sum"].all(),
           deps.loc[~deps["turnout_matches_sum"].astype(bool), ["year", "dep_code", "source_url"]])

    print("\n== Codes and page headings ==")
    for year in YEARS:
        codes = set(deps.loc[deps["year"] == year, "dep_code"])
        report(f"{year}: 2A and 2B present, no plain '20'", {"2A", "2B"} <= codes and "20" not in codes)
        report(f"{year}: no code lost its leading zero", not any(re.fullmatch(r"\d", c) for c in codes))
    name_ok = deps.apply(lambda r: dep.norm_dep_name(r["dep_name"]) in norm_text(r["page_heading"]), axis=1)
    report("département name appears in the page heading", name_ok.all(),
           deps.loc[~name_ok, ["year", "dep_code", "dep_name", "page_heading"]])
    code_in_url = dep_urls["url"].map(dep.code_from_url)
    report("code in URL matches département code", code_in_url.eq(dep_urls["dep_code"]).all(),
           dep_urls.loc[code_in_url.ne(dep_urls["dep_code"]), ["year", "dep_code", "dep_name", "url", "method"]])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild even if the outputs exist")
    parser.add_argument("--offline", action="store_true", help="use cached pages only, never download")
    parser.add_argument("--limit", type=int, help="scrape only the first N départements of each year (testing)")
    args = parser.parse_args()

    if all(p.exists() for p in OUTPUTS) and not args.force:
        print("Outputs already exist — nothing to do (use --force to rebuild):")
        print("\n".join(f"  {p}" for p in OUTPUTS))
        return
    department_pages.offline = department_pages.offline or args.offline

    frames, failures, urls = [], [], []
    for year in YEARS:
        dep_urls = dep.discover_departement_urls(year)
        results, fails = dep.scrape_year(year, dep_urls, limit=args.limit)
        if len(fails):  # retry once (e.g. timeouts)
            results, fails = dep.retry_failures(year, dep_urls, results, fails)
        frames.append(results)
        failures.append(fails)
        urls.append(dep_urls)
    results = dep.combine(frames)
    failures = pd.concat(failures, ignore_index=True)
    dep_urls = pd.concat(urls, ignore_index=True)

    validate(results, dep_urls, failures)

    DEPARTMENT_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    for year, df in zip(YEARS, urls):
        df.to_csv(DEPARTMENT_URLS[year], index=False, encoding="utf-8")
        results[results["year"] == year].to_csv(DEPARTMENT_RAW[year], index=False, encoding="utf-8")
    processed = results.drop(columns=["source_url", "page_heading", "table_round_label", "turnout_matches_sum"])
    processed.to_csv(DEPARTMENT_RESULTS, index=False, encoding="utf-8")
    if not failures.empty:
        failures.to_csv(DEPARTMENT_FAILURES, index=False, encoding="utf-8")
    print(f"\nSaved {len(processed)} rows to {DEPARTMENT_RESULTS} | failures: {len(failures)}")
    show(processed.groupby("year").agg(departements=("dep_code", "nunique"), candidates=("candidate_clean", "nunique")))


if __name__ == "__main__":
    main()
