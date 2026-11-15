"""The official files by polling station (svgeo.datagouv) give the results read on the archive pages.

tests/fixtures/datagouv/ holds excerpts of the two files, in their original layout and encoding: every row of
L'Abergement-Clémenciat (01001), Ajaccio (2A004) and Régina (Guyane, ZC301), plus Abidjan (ZZ001) in 2022.
The first two are also among the saved results pages, so tests/fixtures/expected_pages.json gives the votes and
turnout that the scrapers read for them.
"""
import json
from pathlib import Path

import pytest

from svgeo import datagouv
from svgeo.config import DATAGOUV_FILES, EXPECTED_K, YEARS

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [c for c in json.loads((FIXTURES / "expected_pages.json").read_text(encoding="utf-8"))
         if c["kind"] == "commune" and c["commune_code"] in ("01001", "2A004")]


@pytest.fixture(scope="module")
def bureaux():
    return {year: datagouv.read_bureaux(year, FIXTURES / "datagouv" / DATAGOUV_FILES[year]["path"].name)
            for year in YEARS}


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['year']}-{c['commune_code']}")
def test_polling_stations_add_up_to_the_scraped_commune(case, bureaux):
    year = case["year"]
    communes = datagouv.commune_results(bureaux[year], year)
    df = communes[communes["commune_code"] == case["commune_code"]]
    assert len(df) == EXPECTED_K[year]
    assert dict(zip(df["candidate"], df["votes"])) == case["votes"]  # same names as on the pages, civility included
    assert {k: int(df[k].iloc[0]) for k in case["turnout"]} == case["turnout"]
    assert df["commune_name"].iloc[0] == case["commune_name"]
    assert df["turnout_matches_sum"].all()


@pytest.mark.parametrize("year", YEARS)
def test_tables_have_the_columns_of_the_scraped_files(year, bureaux):
    communes = datagouv.commune_results(bureaux[year], year)
    departments = datagouv.department_results(bureaux[year], year)
    assert list(communes.columns) == datagouv.COMMUNE_COLUMNS
    assert list(departments.columns) == datagouv.DEPARTMENT_COLUMNS
    assert communes["round"].eq(1).all() and departments["round"].eq(1).all()
    # 2002: the file also holds the second round (2 candidates), which must be left out
    assert communes.groupby("commune_code")["candidate"].nunique().eq(EXPECTED_K[year]).all()
    assert not communes.duplicated(["commune_code", "candidate"]).any()


@pytest.mark.parametrize("year", YEARS)
def test_territory_codes(year, bureaux):
    communes = datagouv.commune_results(bureaux[year], year)
    departments = datagouv.department_results(bureaux[year], year)
    expected = {"01001": ("01", "Ain"), "2A004": ("2A", "Corse-du-Sud"), "97301": ("973", "Guyane")}
    found = communes.drop_duplicates("commune_code").set_index("commune_code")
    assert {c: (r.department_code, r.department_name) for c, r in found.iterrows()} == expected
    assert found.loc["97301", "commune_code_source"] == "ZC301"
    # French voters abroad (ZZ, 2022 file only) are a 'département' of the scraped tables but have no commune
    assert set(departments["dep_code"]) == {"01", "2A", "973"} | ({"ZZ"} if year == 2022 else set())


@pytest.mark.parametrize("year", YEARS)
def test_departement_turnout_sums_every_polling_station(year, bureaux):
    """Polling station numbers start again in every commune: stations must not be merged across communes."""
    communes = datagouv.commune_results(bureaux[year], year).drop_duplicates("commune_code")
    departments = datagouv.department_results(bureaux[year], year).drop_duplicates("dep_code").set_index("dep_code")
    for code, group in communes.groupby("department_code"):
        row = departments.loc[code]
        assert (row["inscrits"], row["votants"], row["exprimes"]) == tuple(
            group[["registered", "voters", "expressed"]].sum())
        assert row["blancs_nuls"] == group["blank_null"].sum() and row["abstentions"] == group["abstentions"].sum()
    by_dep = datagouv.department_results(bureaux[year], year).groupby("dep_code")
    assert by_dep["votes"].sum().eq(by_dep["exprimes"].first()).all()
    assert by_dep["vote_share_calc"].sum().sub(100).abs().lt(1e-9).all()


def test_commune_code():
    assert datagouv.commune_code("01", "1") == "01001"
    assert datagouv.commune_code("2A", "004") == "2A004"
    assert datagouv.commune_code("ZA", "101") == "97101"
    assert datagouv.commune_code("ZN", "801") == "98801"
    assert datagouv.commune_code("ZZ", "001") is None


def test_compare_reports_every_kind_of_difference(bureaux):
    ours = datagouv.commune_results(bureaux[2002], 2002)
    turnout = ["registered", "voters", "expressed"]
    same = datagouv.compare(ours, ours.copy(), "commune_code", turnout)
    assert all(table.empty for table in same.values())

    theirs = ours[ours["commune_code"] != "97301"].copy()
    theirs["candidate_clean"] = theirs["candidate_clean"].str.replace(" ", "  ")  # spacing of the scraped names
    row = theirs.index[(theirs["commune_code"] == "01001") & theirs["candidate"].str.endswith("JOSPIN")][0]
    theirs.loc[row, "votes"] += 1
    theirs.loc[theirs["commune_code"] == "2A004", "registered"] += 10
    diff = datagouv.compare(ours, theirs, "commune_code", turnout)
    assert list(diff["only_ours"]["commune_code"]) == ["97301"] and diff["only_theirs"].empty
    assert diff["votes"][["commune_code", "candidate", "votes_ours", "votes_theirs"]].values.tolist() == [
        ["01001", "LIONEL JOSPIN", 60, 61]]
    assert list(diff["turnout"]["commune_code"]) == ["2A004"]
