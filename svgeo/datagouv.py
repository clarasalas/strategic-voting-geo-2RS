"""First-round results from the official files by polling station (bureau de vote) published on data.gouv.fr.

An alternative to scraping the results archive (svgeo.departements, svgeo.communes): the Ministère de l'Intérieur
publishes one file per election with every polling station, and summing the polling stations gives the same commune
and département results as the archive pages. The tables built here have the columns of the scraped ones, so the
rest of the pipeline (scripts 03 and 04) reads either.

The two files have different layouts, both in Latin-1 with `;` as separator:

* 2002, `PR02_BVot_T1T2.txt`: comment lines starting with `--`, then one row per round × polling station × candidate:
  round; département; commune number; commune name; polling station; registered; voters; expressed; candidate number;
  surname; first name; short code; votes. Blank and null ballots are not given (voters − expressed).
* 2022, `resultats-par-niveau-burvot-t1-france-entiere.txt`: a header, then one row per polling station with 21
  turnout fields followed by one block of 7 fields per candidate (ballot number; sex; surname; first name; votes;
  % of registered; % of expressed). First round only.

Overseas territories have the Ministry's letter codes (ZA = Guadeloupe, …), translated to the codes used by the
scrapers. Paris, Lyon and Marseille are whole communes in both files (no arrondissement codes).
"""
import hashlib

import pandas as pd
import requests

from svgeo.communes import OVERSEAS_LETTER_PREFIX, RAW_COLUMNS
from svgeo.config import DATAGOUV_FILES
from svgeo.departements import DEPARTEMENTS, RESULT_COLUMNS

# Ministry letter code -> département code of the scraped tables (ZX and ZZ keep their code there too)
TERRITORY_CODES = {"ZA": "971", "ZB": "972", "ZC": "973", "ZD": "974", "ZM": "976", "ZS": "975",
                   "ZN": "988", "ZP": "987", "ZW": "986"}
# The 2002 file has no civility column; the results pages write 'Mme' for these candidates
WOMEN_2002 = {"LAGUILLER", "LEPAGE", "TAUBIRA", "BOUTIN"}
N_TURNOUT_FIELDS_2022, CANDIDATE_BLOCK_2022 = 21, 7

STATION = ["territory", "commune_number", "bureau"]  # polling station numbers start again in every commune
BUREAU_COLUMNS = ["territory", "commune_number", "commune_name", "bureau", "registered", "voters", "expressed",
                  "ballot_order", "candidate", "candidate_surname", "votes"]
# Processed files do not keep the page diagnostics of the scraped département table
DEPARTMENT_COLUMNS = [c for c in RESULT_COLUMNS
                      if c not in ("source_url", "page_heading", "table_round_label", "turnout_matches_sum")]
COMMUNE_COLUMNS = [*RAW_COLUMNS, "candidate_clean"]


# ---- Download ----
def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(year, force=False):
    """Path of the official file of `year`, downloaded if it is not in data/raw/datagouv/ yet.

    The file must have the recorded checksum: data.gouv.fr keeps the files of past elections unchanged, so a
    different file means a truncated download or a new version, whose layout should be checked first.
    """
    spec = DATAGOUV_FILES[year]
    path = spec["path"]
    if force or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        with requests.get(spec["url"], stream=True, timeout=60) as response:
            response.raise_for_status()
            with open(partial, "wb") as f:
                for block in response.iter_content(1 << 20):
                    f.write(block)
        partial.replace(path)
        print(f"{year}: downloaded {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
    actual = sha256(path)
    if actual != spec["sha256"]:
        raise ValueError(f"{path}: sha256 {actual} differs from the expected {spec['sha256']} "
                         f"(delete the file to download it again from {spec['url']})")
    return path


# ---- Reading the files: one row per polling station × candidate, first round ----
def read_bureaux_2002(path):
    names = ["round", "territory", "commune_number", "commune_name", "bureau", "registered", "voters", "expressed",
             "ballot_order", "candidate_surname", "first_name", "short_code", "votes"]
    df = pd.read_csv(path, sep=";", names=names, dtype=str, encoding="latin-1", quoting=3, keep_default_na=False)
    df = df[~df["round"].str.startswith("--")]  # comment lines describing the fields
    df = df[df["round"].str.strip() == "1"].copy()
    civility = df["candidate_surname"].map(lambda s: "Mme" if s in WOMEN_2002 else "M.")
    df["candidate"] = civility + " " + df["first_name"] + " " + df["candidate_surname"]
    return _finish(df)


def read_bureaux_2022(path):
    rows = []
    with open(path, encoding="latin-1") as f:
        header = f.readline().rstrip("\r\n").split(";")
        expected = ["Code du département", "Code de la commune", "Libellé de la commune", "Code du b.vote",
                    "Inscrits", "Votants", "Exprimés", "N°Panneau", "Sexe", "Nom", "Prénom", "Voix"]
        found = [header[i] for i in (0, 4, 5, 6, 7, 10, 18, 21, 22, 23, 24, 25)]
        if found != expected:
            raise ValueError(f"{path}: unexpected header {found}")
        for line in f:
            p = line.rstrip("\r\n").split(";")
            if len(p) < N_TURNOUT_FIELDS_2022:
                continue
            station = (p[0], p[4], p[5], p[6], p[7], p[10], p[18])
            for k in range(N_TURNOUT_FIELDS_2022, len(p) - CANDIDATE_BLOCK_2022 + 1, CANDIDATE_BLOCK_2022):
                order, sex, surname, first_name, votes = p[k:k + 5]
                civility = "Mme" if sex == "F" else "M."
                rows.append((*station, order, f"{civility} {first_name} {surname}", surname, votes))
    return _finish(pd.DataFrame(rows, columns=BUREAU_COLUMNS))


def _finish(df):
    df = df[BUREAU_COLUMNS].copy()
    for column in ["registered", "voters", "expressed", "ballot_order", "votes"]:
        df[column] = df[column].astype(int)
    for column in ["territory", "commune_number", "bureau"]:
        df[column] = df[column].str.strip()
    return df.reset_index(drop=True)


READERS = {2002: read_bureaux_2002, 2022: read_bureaux_2022}


def read_bureaux(year, path=None):
    """First-round rows of the official file of `year` (downloaded if needed when no `path` is given)."""
    return READERS[year](fetch(year) if path is None else path)


# ---- Aggregation ----
def department_code(territory):
    return TERRITORY_CODES.get(territory, territory)


def commune_code(territory, number):
    """INSEE commune code, as svgeo.communes builds it from the page names; None outside communes (ZZ: consulates)."""
    number = number.zfill(3)
    if territory in OVERSEAS_LETTER_PREFIX:
        return OVERSEAS_LETTER_PREFIX[territory] + number
    return None if territory.startswith("Z") else territory + number


def _sum_bureaux(bureaux, unit):
    """Turnout and candidate votes of each unit (`unit`: columns identifying it), one row per unit × candidate."""
    stations = bureaux.drop_duplicates(STATION)
    turnout = stations.groupby(unit, sort=False)[["registered", "voters", "expressed"]].sum()
    votes = (bureaux.groupby([*unit, "candidate"], sort=False)
                    .agg(votes=("votes", "sum"), candidate_surname=("candidate_surname", "first"),
                         ballot_order=("ballot_order", "first")).reset_index())
    out = votes.merge(turnout.reset_index(), on=unit, how="left", validate="many_to_one")
    out["abstentions"] = out["registered"] - out["voters"]
    out["blank_null"] = out["voters"] - out["expressed"]
    # The results pages publish this share rounded to 2 decimals; here it is recomputed from the counts
    out["vote_share_calc"] = 100 * out["votes"] / out["expressed"].where(out["expressed"] > 0)
    out["vote_share"] = out["vote_share_calc"].round(2)
    out["candidate_clean"] = out["candidate"].str.replace(r"^(M\.|Mme)\s+", "", regex=True)
    out["turnout_matches_sum"] = out.groupby(unit, sort=False)["votes"].transform("sum") == out["expressed"]
    return out


def department_results(bureaux, year):
    """One row per département × candidate, with the columns of data/processed/presidential_departments_*.csv."""
    bureaux = bureaux.assign(dep_code=bureaux["territory"].map(department_code))
    out = _sum_bureaux(bureaux, ["dep_code"]).merge(DEPARTEMENTS, on="dep_code", how="left")
    out = out.rename(columns={"registered": "inscrits", "voters": "votants", "expressed": "exprimes",
                              "blank_null": "blancs_nuls"})
    out["year"], out["round"] = year, 1
    return (out[DEPARTMENT_COLUMNS].sort_values(["dep_code", "votes"], ascending=[True, False], kind="stable")
            .reset_index(drop=True))


def commune_results(bureaux, year, source_url=None):
    """One row per commune × candidate, with the columns of data/processed/presidential_communes_*.csv.

    Columns that describe a scraped page are filled with their equivalent: `source_url` is the address of the
    official file, `commune_code_source` the Ministry's own code ('ZA101', '01001'), `page_heading` is empty.
    """
    codes = pd.Series([commune_code(t, n) for t, n in zip(bureaux["territory"], bureaux["commune_number"])],
                      index=bureaux.index, dtype=object)
    bureaux = bureaux[codes.notna()].assign(
        commune_code=codes[codes.notna()],
        commune_code_source=lambda d: d["territory"] + d["commune_number"].str.zfill(3),
        department_code=lambda d: d["territory"].map(department_code))
    out = _sum_bureaux(bureaux, ["commune_code"])
    info = (bureaux.drop_duplicates("commune_code")
            [["commune_code", "commune_code_source", "commune_name", "department_code"]]
            .merge(DEPARTEMENTS.rename(columns={"dep_code": "department_code", "dep_name": "department_name"})
                   [["department_code", "department_name"]], on="department_code", how="left"))
    out = out.merge(info, on="commune_code", how="left", validate="many_to_one")
    out["year"], out["round"] = year, 1
    out["page_heading"] = ""
    out["source_url"] = DATAGOUV_FILES[year]["url"] if source_url is None else source_url
    return out.sort_values(["commune_code", "ballot_order"], kind="stable")[COMMUNE_COLUMNS].reset_index(drop=True)


# ---- Comparison with another copy of the results (e.g. the scraped ones) ----
def _key(name):
    """Candidate name without civility, case or repeated spaces ('M.  JEAN-MARIE  LE PEN' in the scraped pages)."""
    return " ".join(str(name).upper().split())


def compare(ours, theirs, unit, turnout):
    """Differences between two result tables with one row per year × `unit` × candidate.

    `turnout`: the turnout columns to compare (repeated on every row of a unit). Returns
    {"only_ours", "only_theirs"} (units in one table only), "votes" (unit × candidate rows whose votes differ or that
    are in one table only) and "turnout" (units whose turnout differs).
    """
    keys = ["year", unit]
    a, b = (t.assign(_candidate=t["candidate_clean"].map(_key)) for t in (ours, theirs))
    units_a, units_b = (t[keys].drop_duplicates() for t in (a, b))
    units = units_a.merge(units_b, on=keys, how="outer", indicator=True)
    both = units.loc[units["_merge"] == "both", keys]
    votes = (a.merge(both, on=keys)[[*keys, "_candidate", "votes"]]
              .merge(b.merge(both, on=keys)[[*keys, "_candidate", "votes"]], on=[*keys, "_candidate"], how="outer",
                     suffixes=("_ours", "_theirs")))
    votes = votes[votes["votes_ours"].ne(votes["votes_theirs"])]
    t = (a.drop_duplicates(keys)[[*keys, *turnout]]
          .merge(b.drop_duplicates(keys)[[*keys, *turnout]], on=keys, suffixes=("_ours", "_theirs")))
    differs = pd.concat([t[f"{c}_ours"].ne(t[f"{c}_theirs"]) & ~(t[f"{c}_ours"].isna() & t[f"{c}_theirs"].isna())
                         for c in turnout], axis=1).any(axis=1)
    return {"only_ours": units.loc[units["_merge"] == "left_only", keys],
            "only_theirs": units.loc[units["_merge"] == "right_only", keys],
            "votes": votes.rename(columns={"_candidate": "candidate"}), "turnout": t[differs]}
