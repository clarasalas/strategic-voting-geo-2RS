# strategic-voting-geo-2RS

Geography of strategic voting in French two-round presidential elections: is **electoral coordination** in the first
round (2002 and 2022) associated with **population density**, across départements and across communes?

Coordination is measured from each unit's first-round vote shares: ENP, **CENP** (main measure,
`(K − ENP) / (K − 1)`), and the "cliff" measures (magnitude, location, ratio of the largest drop between consecutive
ranked shares). All results are descriptive.

## Repository layout

```
svgeo/                       shared code (pip install -e .)
  config.py                  paths and constants (years, K, metropolitan codes, Paris/Lyon/Marseille)
  web.py                     page download with an on-disk cache
  results_page.py            parsing a results page (first-round candidates and turnout)
  departements.py            département scraper
  communes.py                commune scraper
  indices.py                 ENP, CENP, cliff measures
  analysis.py                association statistics and plots
scripts/                     build the data, in order
  01_scrape_departements.py
  02_scrape_communes.py
  03_coordination_indices.py
  04_density.py
notebooks/                   analysis (read the processed CSVs only)
  analysis_departements.ipynb
  analysis_communes.ipynb
  audit_communes.ipynb       robustness audit of the commune result
tests/
```

## Pipeline

| step | reads | writes |
|---|---|---|
| `01_scrape_departements.py` | election archive | `raw/presidential_{year}_department_urls.csv`, `raw/presidential_{year}_departments.csv`, `processed/presidential_departments_2002_2022.csv` |
| `02_scrape_communes.py` | département URLs, election archive | `raw/presidential_{year}_communes.csv`, `raw/presidential_{year}_communes_failures.csv`, `processed/presidential_communes_2002_2022.csv` |
| `03_coordination_indices.py` | results | `processed/coordination_indices_{departements,communes}.csv` |
| `04_density.py` | indices, INSEE files | `processed/coordination_density_departements.csv`, `processed/coordination_density_departements_wide.csv`, `processed/coordination_density_communes_corrected.csv` (+ one file per year) |
| `notebooks/audit_communes.ipynb` | commune results and density | `processed/audit_corrected/*.csv` |

All paths are under `data/`. **A script skips any output that already exists**, so running everything again rebuilds
only what is missing; pass `--force` to rebuild anyway. Scraped pages are cached in `data/raw/html_cache*/`, so
scraping runs again from disk with `--offline` (no download at all).

```bash
pip install -r requirements.txt
pip install -e .
python scripts/01_scrape_departements.py
python scripts/02_scrape_communes.py        # several hours per year without the HTML cache
python scripts/03_coordination_indices.py
python scripts/04_density.py
pytest
```

Set `SVGEO_DATA_DIR=/path/to/data` to work on another copy of the data folder.

## Data

`data/` is not versioned (the commune results alone are ~260 MB). To rebuild it:

* **Election results** — scraped from the Ministère de l'Intérieur archive,
  <https://www.archives-resultats-elections.interieur.gouv.fr/> (presidential elections 2002 and 2022, first round).
* **INSEE files**, downloaded by hand into `data/raw/insee/`:
  * `1_Pop_annu_compo_evol_depreg.xlsx` — *Estimations de population : population annuelle et composantes de
    l'évolution démographique par département et région* (population on 1 January, one sheet per year);
  * `base-cc-serie-historique-2022.CSV` and `meta_base-cc-serie-historique-2022.CSV` — *Séries historiques du
    recensement, base communale* (commune populations `D99_POP`, `P06_POP`, `P22_POP` and surface `SUPERF`).

## Main choices

* **K** = number of candidates nationally (16 in 2002, 12 in 2022); a candidate with no vote in a unit has share 0.
* **Main samples**: the 96 metropolitan départements; metropolitan communes matched to INSEE, excluding 2002 communes
  flagged as possible boundary changes (registered voters / population outside [0.4, 1.2]).
* **Commune density**: 2022 population for 2022; for 2002, a geometric interpolation between the 1999 census and 2006
  (`population_2002_interp`), on the recent INSEE commune geography.
* **Paris, Lyon, Marseille** are whole communes; arrondissement pages are only used to check that they add up.
* 2002 and 2022 are two separate cross-sections, not a panel.
