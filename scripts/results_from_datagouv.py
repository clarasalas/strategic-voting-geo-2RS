"""Steps 1 and 2 without scraping — first-round results from the official files by polling station.

The Ministère de l'Intérieur publishes on data.gouv.fr one file per election with the results of every polling
station (bureau de vote). Summing the polling stations gives the commune and département results of the archive
pages read by scripts 01 and 02, in a few minutes instead of several hours. The two processed files written here
have the same columns as the scraped ones, so scripts 03 and 04 run on either.

Inputs (downloaded into data/raw/datagouv/ if missing, checked against their checksum)
  PR02_BVot_T1T2.txt                                   2002, both rounds
  resultats-par-niveau-burvot-t1-france-entiere.txt    2022, first round

Outputs
  data/processed/presidential_departments_2002_2022.csv   one row per year × département × candidate
  data/processed/presidential_communes_2002_2022.csv      one row per year × commune × candidate

Differences from the scraped files: `vote_share` is recomputed from the counts (the pages publish it rounded),
`source_url` is the address of the official file, `page_heading` is empty, and the territories covered outside
metropolitan France are those of each file (the 2002 file has the four overseas départements only). The scraping
by-products (data/raw/presidential_*.csv, the failure files, the HTML cache) are not produced: the part of
notebooks/audit_communes.ipynb that audits the scraping needs scripts 01 and 02.

Usage: python scripts/results_from_datagouv.py [--force] [--check]
  --check   write nothing: compare with the processed files already in data/ (e.g. the scraped ones)
"""
import argparse
import sys

import pandas as pd

from svgeo import datagouv
from svgeo.config import COMMUNE_RESULTS, DEPARTMENT_RESULTS, EXPECTED_K, METRO_CODES, PLM, YEARS
from svgeo.utils import read_csv, report, show

OUTPUTS = [DEPARTMENT_RESULTS, COMMUNE_RESULTS]
DEPARTMENT_TURNOUT = ["inscrits", "votants", "exprimes"]
COMMUNE_TURNOUT = ["registered", "voters", "expressed"]


def build():
    departments, communes = [], []
    for year in YEARS:
        bureaux = datagouv.read_bureaux(year)
        stations = bureaux.drop_duplicates(datagouv.STATION)
        print(f"{year}: {len(stations)} polling stations, {int(stations['expressed'].sum()):,} expressed votes")
        by_station = bureaux.groupby(datagouv.STATION).agg(
            votes=("votes", "sum"), expressed=("expressed", "first"), candidates=("candidate", "nunique"))
        report(f"{year}: candidate votes sum to expressed in every polling station",
               by_station["votes"].eq(by_station["expressed"]).all(),
               by_station[by_station["votes"] != by_station["expressed"]])
        report(f"{year}: every polling station lists the {EXPECTED_K[year]} candidates",
               by_station["candidates"].eq(EXPECTED_K[year]).all(),
               by_station[by_station["candidates"] != EXPECTED_K[year]])
        departments.append(datagouv.department_results(bureaux, year))
        communes.append(datagouv.commune_results(bureaux, year))
    return pd.concat(departments, ignore_index=True), pd.concat(communes, ignore_index=True)


def validate(departments, communes):
    print("\n== Départements ==")
    for year in YEARS:
        codes = set(departments.loc[departments["year"] == year, "dep_code"])
        report(f"{year}: the 96 metropolitan départements are present", set(METRO_CODES) <= codes,
               pd.Series(sorted(set(METRO_CODES) - codes), dtype=str))
        print(f"   other territories included: {sorted(codes - set(METRO_CODES))}")
    report("every département has a name", departments["dep_name"].notna().all(),
           departments.loc[departments["dep_name"].isna(), ["year", "dep_code"]].drop_duplicates())
    dup = departments.duplicated(["year", "dep_code", "candidate"], keep=False)
    report("no duplicated year × département × candidate rows", not dup.any(), departments[dup])

    print("\n== Communes ==")
    dup = communes.duplicated(["year", "commune_code", "candidate"], keep=False)
    report("no duplicated year × commune × candidate rows", not dup.any(), communes[dup])
    report("commune codes have 5 characters", communes["commune_code"].str.len().eq(5).all())
    n_cand = communes.groupby(["year", "commune_code"])["candidate_clean"].nunique().reset_index(name="n")
    report("every commune lists all K candidates", n_cand["n"].eq(n_cand["year"].map(EXPECTED_K)).all(),
           n_cand[n_cand["n"] != n_cand["year"].map(EXPECTED_K)])
    report("candidate votes sum to expressed in every commune", communes["turnout_matches_sum"].all(),
           communes.loc[~communes["turnout_matches_sum"], ["year", "commune_code"]].drop_duplicates())
    for year in YEARS:
        present = set(communes.loc[communes["year"] == year, "commune_code"])
        report(f"{year}: Paris, Lyon and Marseille are whole communes", set(PLM) <= present)
    metro = communes[communes["department_code"].isin(METRO_CODES)]
    sums = metro.groupby(["year", "department_code", "candidate_clean"])["votes"].sum()
    deps = departments.set_index(["year", "dep_code", "candidate_clean"])["votes"]
    report("communes add up to their département (metropolitan France): votes",
           sums.eq(deps.reindex(sums.index).to_numpy()).all())
    by_commune = metro.drop_duplicates(["year", "commune_code"])
    turnout = by_commune.groupby(["year", "department_code"])[COMMUNE_TURNOUT].sum()
    dep_turnout = departments.drop_duplicates(["year", "dep_code"]).set_index(["year", "dep_code"])[DEPARTMENT_TURNOUT]
    report("communes add up to their département (metropolitan France): registered, voters, expressed",
           (turnout.to_numpy() == dep_turnout.reindex(turnout.index).to_numpy()).all())
    report("candidate votes sum to expressed in every département",
           departments.groupby(["year", "dep_code"])["votes"].sum()
           .eq(departments.groupby(["year", "dep_code"])["exprimes"].first()).all())
    print("\nCommunes and départements per year:")
    show(communes.groupby("year").agg(communes=("commune_code", "nunique"),
                                      metropolitan=("commune_code", lambda c: c[c.str[:2].isin(METRO_CODES)].nunique()),
                                      departements=("department_code", "nunique")))


def check(departments, communes):
    """Compare with the processed files already in the data folder; True if nothing differs."""
    missing = [p for p in OUTPUTS if not p.exists()]
    if missing:
        raise SystemExit("--check needs the processed files to compare with; missing:\n"
                         + "\n".join(f"  {p}" for p in missing))
    identical = True
    for label, ours, path, unit, turnout in [
            ("départements", departments, DEPARTMENT_RESULTS, "dep_code", DEPARTMENT_TURNOUT),
            ("communes", communes, COMMUNE_RESULTS, "commune_code", COMMUNE_TURNOUT)]:
        theirs = read_csv(path)
        diff = datagouv.compare(ours, theirs, unit, turnout)
        n_units = len(theirs.drop_duplicates(["year", unit]))
        print(f"\n== {label}: data.gouv.fr files vs {path.name} ({n_units} year × {unit} units) ==")
        # Territories outside metropolitan France are not the same in the two sources: reported, not a failure
        for side, name in [("only_ours", "only in the data.gouv.fr files"), ("only_theirs", f"only in {path.name}")]:
            codes = diff[side]
            metro = codes[codes[unit].str[:2].isin(METRO_CODES)]
            report(f"no metropolitan unit {name} ({len(codes) - len(metro)} elsewhere)", metro.empty, metro)
            identical &= metro.empty
        report(f"candidate votes identical ({len(diff['votes'])} differing rows)", diff["votes"].empty, diff["votes"])
        report(f"{', '.join(turnout)} identical ({len(diff['turnout'])} differing units)", diff["turnout"].empty,
               diff["turnout"])
        identical &= diff["votes"].empty and diff["turnout"].empty
    return identical


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild even if the outputs exist")
    parser.add_argument("--check", action="store_true", help="compare with the existing processed files, write nothing")
    args = parser.parse_args()

    if not args.check and all(p.exists() for p in OUTPUTS) and not args.force:
        print("Outputs already exist — nothing to do (use --force to rebuild, --check to compare with them):")
        print("\n".join(f"  {p}" for p in OUTPUTS))
        return

    departments, communes = build()
    validate(departments, communes)
    if args.check:
        identical = check(departments, communes)
        print("\nSame results in both sources." if identical else "\nThe two sources differ: see [CHECK] lines above.")
        sys.exit(0 if identical else 1)

    DEPARTMENT_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    for table, path in [(departments, DEPARTMENT_RESULTS), (communes, COMMUNE_RESULTS)]:
        if path.exists() and not args.force:
            print(f"{path.name} exists — kept (use --force to replace it)")
            continue
        table.to_csv(path, index=False, encoding="utf-8")
        print(f"Saved {len(table)} rows to {path}")


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    main()
