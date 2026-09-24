# Geography of electoral coordination in France: data

Data for the project *Geography of electoral coordination in France*
(<https://github.com/clarasalas/strategic-voting-geo-2RS>), by Clara Salas (Master's thesis project, ENS-PSL /
Centre Borelli). The project measures how concentrated the first-round vote was in each metropolitan département and
commune in the 2002 and 2022 French presidential elections, and how that concentration relates to population density.
It is a complementary analysis to the agent-based model
[strategic-voting-abm-2RS](https://github.com/clarasalas/strategic-voting-abm-2RS).

This deposit is a frozen copy of every input and output of the analysis, so that it can be reproduced without
scraping the election results again (several hours) and even if the source websites change.

## Files

Each archive stores its files under their path in the repository (`data/raw/...`, `data/processed/...`).

| file | contents |
|---|---|
| `insee_inputs.zip` | INSEE files used for population, surface and the urban–rural classification (`data/raw/insee/`) |
| `raw_results.zip` | first-round results scraped from the Ministère de l'Intérieur archive, by département and by commune, with the département page URLs and the pages that could not be read (`data/raw/*.csv`) |
| `processed.zip` | outputs of the pipeline: candidate results, concentration indices (HHI, corrected HHI, ENP, CENP, cliff measures), indices merged with population density, and the tables of the commune audit (`data/processed/`) |
| `html_pages.zip` | every scraped results page, as downloaded (`data/raw/html_cache*/`); lets the scraping scripts run again with `--offline` |
| `SHA256SUMS` | SHA-256 checksums of the files above |

## Use

In a clone of the GitHub repository:

```bash
pip install -r requirements.txt && pip install -e .
python scripts/download_data.py                     # add --with-html-pages for the scraped pages
```

or unzip the archives at the root of the clone. The notebooks then run directly; the scripts rebuild the processed
files from the raw ones (`python scripts/03_coordination_indices.py --force`, then `04_density.py --force`).

## Sources and licences

* **Election results**: Ministère de l'Intérieur, archive of election results,
  <https://www.archives-resultats-elections.interieur.gouv.fr/> (presidential elections 2002 and 2022, first round),
  Licence Ouverte.
* **INSEE** (Licence Ouverte / Open Licence 2.0):
  * `1_Pop_annu_compo_evol_depreg.xlsx`: *La situation démographique en 2025*, table DEP1, population on 1 January
    by département, <https://www.insee.fr/fr/statistiques/8999023?sommaire=8999231>;
  * `base-cc-serie-historique-2022.CSV` and `meta_base-cc-serie-historique-2022.CSV`: *Séries historiques en 2022*,
    commune populations and surfaces (geography of 1 January 2025), <https://www.insee.fr/fr/statistiques/8582555>;
  * `grille_densite_2025_geo2025.xlsx`: *La grille de densité 2025* (file `fichier_diffusion_2025.xlsx`, geography
    of 1 January 2025), <https://www.insee.fr/fr/information/8571524>.
* **Not included**: the département boundaries used for the maps, Etalab *contours administratifs* 2025
  (`departements-100m.geojson`, ODbL), <https://etalab-datasets.geo.data.gouv.fr/contours-administratifs/2025/geojson/departements-100m.geojson>.
  `download_data.py` downloads them and checks their checksum.

The deposit is released under CC BY 4.0. The source data remain under their own licences, which require crediting
the Ministère de l'Intérieur and INSEE.
