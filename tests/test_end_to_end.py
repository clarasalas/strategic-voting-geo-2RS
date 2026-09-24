"""End-to-end checks.

1. Parsing (always runs, no network): a few saved results pages (tests/fixtures/, département and commune pages of
   both elections, Paris as a whole commune) go through the same functions as the scrapers, and must give the votes
   and turnout recorded in the scraped data (tests/fixtures/expected_pages.json).
2. Pipeline (runs only when data/ holds the scraped results and the INSEE files; `pytest -m "not slow"` skips it):
   scripts 03 and 04 rebuild the indices and the density files in a temporary folder, and the outputs must be
   byte-identical to data/processed/.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from svgeo import communes, departements
from svgeo.config import (COMMUNE_DENSITY, COMMUNE_DENSITY_BY_YEAR, COMMUNE_INDICES, COMMUNE_RESULTS,
                          DEPARTMENT_DENSITY, DEPARTMENT_DENSITY_WIDE, DEPARTMENT_INDICES, DEPARTMENT_RESULTS,
                          EXPECTED_K, INSEE_DIR, ROOT)
from svgeo.web import Fetcher

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "expected_pages.json").read_text(encoding="utf-8"))


@pytest.fixture
def saved_pages(monkeypatch):
    """The scrapers read pages from the fixture folders and never download."""
    for module, folder, compressed in [(departements, "departements", False), (communes, "communes", True)]:
        fetcher = Fetcher(FIXTURES / folder, compressed=compressed, delay=0)
        fetcher.offline = True
        monkeypatch.setattr(module, "pages", fetcher)


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['kind']}-{c['year']}-{c.get('dep_code') or c['commune_code']}")
def test_saved_page_gives_the_scraped_results(case, saved_pages):
    year = case["year"]
    if case["kind"] == "departement":
        df = departements.parse_departement(case["url"], year, case["dep_code"], case["dep_name"])
    else:
        df = communes.parse_commune_results(case["url"], year, {"commune_code": case["commune_code"],
                                                                "commune_name": case["commune_name"]})
    assert len(df) == EXPECTED_K[year]  # first round, every candidate
    assert dict(zip(df["candidate"], df["votes"])) == case["votes"]
    assert {k: int(df[k].iloc[0]) for k in case["turnout"]} == case["turnout"]
    assert df["turnout_matches_sum"].all()  # the turnout block belongs to the first round


PIPELINE_INPUTS = [DEPARTMENT_RESULTS, COMMUNE_RESULTS]
PIPELINE_OUTPUTS = [DEPARTMENT_INDICES, COMMUNE_INDICES, DEPARTMENT_DENSITY, DEPARTMENT_DENSITY_WIDE, COMMUNE_DENSITY,
                    *COMMUNE_DENSITY_BY_YEAR.values()]


@pytest.mark.slow
@pytest.mark.skipif(not all(p.exists() for p in [*PIPELINE_INPUTS, *PIPELINE_OUTPUTS]) or not INSEE_DIR.exists(),
                    reason="needs the data folder (scripts 01–04 or scripts/download_data.py)")
def test_pipeline_rebuilds_the_processed_files(tmp_path):
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    for path in PIPELINE_INPUTS:
        shutil.copy(path, data / "processed" / path.name)
    shutil.copytree(INSEE_DIR, data / "raw" / "insee")

    env = {**os.environ, "SVGEO_DATA_DIR": str(data)}
    for script in ["03_coordination_indices.py", "04_density.py"]:
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr[-3000:]

    for path in PIPELINE_OUTPUTS:
        assert (data / "processed" / path.name).read_bytes() == path.read_bytes(), f"{path.name} differs"
