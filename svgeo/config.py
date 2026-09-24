"""Paths and constants shared by the scripts and notebooks.

All paths derive from DATA_DIR (default: `data/` at the repository root). Set the environment variable
SVGEO_DATA_DIR to work on another copy of the data.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("SVGEO_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
INSEE_DIR = RAW_DIR / "insee"
AUDIT_DIR = PROCESSED_DIR / "audit_corrected"

# HTML caches: every page is downloaded at most once
DEPARTMENT_CACHE_DIR = RAW_DIR / "html_cache"
COMMUNE_CACHE_DIR = RAW_DIR / "html_cache_communes"  # gzip-compressed (≈ 76,000 pages)

YEARS = [2002, 2022]

# ---- Raw files (scraping outputs) ----
DEPARTMENT_URLS = {y: RAW_DIR / f"presidential_{y}_department_urls.csv" for y in YEARS}
DEPARTMENT_RAW = {y: RAW_DIR / f"presidential_{y}_departments.csv" for y in YEARS}
DEPARTMENT_FAILURES = RAW_DIR / "presidential_departments_failures.csv"  # written only if a page failed
COMMUNE_RAW = {y: RAW_DIR / f"presidential_{y}_communes.csv" for y in YEARS}
COMMUNE_FAILURES = {y: RAW_DIR / f"presidential_{y}_communes_failures.csv" for y in YEARS}

# ---- INSEE inputs (downloaded by hand, see README) ----
INSEE_POP_DEPARTEMENTS = INSEE_DIR / "1_Pop_annu_compo_evol_depreg.xlsx"
INSEE_COMMUNES = INSEE_DIR / "base-cc-serie-historique-2022.CSV"
INSEE_COMMUNES_META = INSEE_DIR / "meta_base-cc-serie-historique-2022.CSV"
# Grille de densité 2025 (geography 1 January 2025, the same as the commune file), saved under this name
INSEE_DENSITY_GRID = INSEE_DIR / "grille_densite_2025_geo2025.xlsx"
# Département boundaries (GeoJSON with a `code` property, downloaded by hand)
DEPARTMENT_BOUNDARIES = RAW_DIR / "geography" / "departements-100m.geojson"

# ---- Processed files ----
DEPARTMENT_RESULTS = PROCESSED_DIR / "presidential_departments_2002_2022.csv"
COMMUNE_RESULTS = PROCESSED_DIR / "presidential_communes_2002_2022.csv"
DEPARTMENT_INDICES = PROCESSED_DIR / "coordination_indices_departements.csv"
COMMUNE_INDICES = PROCESSED_DIR / "coordination_indices_communes.csv"
DEPARTMENT_DENSITY = PROCESSED_DIR / "coordination_density_departements.csv"
DEPARTMENT_DENSITY_WIDE = PROCESSED_DIR / "coordination_density_departements_wide.csv"
# "_corrected": 2002 density from population_2002_interp (earlier versions used the 1999 census count)
COMMUNE_DENSITY = PROCESSED_DIR / "coordination_density_communes_corrected.csv"
COMMUNE_DENSITY_BY_YEAR = {y: PROCESSED_DIR / f"coordination_density_communes_{y}_corrected.csv" for y in YEARS}

# Codes must stay text ('01', '2A', '971'): pass these dtypes to pd.read_csv
CODE_DTYPES = {"dep_code": str, "department_code": str, "commune_code": str, "commune_code_source": str,
               "region_code": str}

# ---- INSEE density grid (grille communale de densité 2025), ordered from most to least dense ----
DENSITY_GRID_LEVELS = {1: "dense urban", 2: "intermediate urban", 3: "rural"}
# The same, with rural communes split by whether they belong to a city's commuter area (aire d'attraction des villes)
DENSITY_GRID_AAV_LEVELS = {1: "dense urban", 2: "intermediate urban", 3: "periurban rural", 4: "non-periurban rural"}

# ---- Elections ----
EXPECTED_K = {2002: 16, 2022: 12}  # first-round candidates nationally (round 2 always has 2)
# Candidates who ran only in the first round: finding them on a page proves round 1 was parsed
FIRST_ROUND_ONLY = {2002: ["JOSPIN", "BAYROU"], 2022: ["ZEMMOUR", "PECRESSE"]}

# The 96 metropolitan départements: 01–95 without 20, plus 2A and 2B
METRO_CODES = sorted([f"{i:02d}" for i in range(1, 96) if i != 20] + ["2A", "2B"])
assert len(METRO_CODES) == 96

# Paris, Lyon, Marseille: whole communes and their municipal arrondissements (INSEE codes)
PLM = {"75056": "Paris", "69123": "Lyon", "13055": "Marseille"}
ARRONDISSEMENTS = ({f"751{i:02d}" for i in range(1, 21)}      # Paris 1er–20e
                   | {f"6938{i}" for i in range(1, 10)}       # Lyon 1er–9e
                   | {f"132{i:02d}" for i in range(1, 17)})   # Marseille 1er–16e
