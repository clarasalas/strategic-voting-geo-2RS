"""Step 4 — population density (INSEE) merged with the coordination indices, by département and by commune.

INSEE inputs (data/raw/insee/, downloaded by hand — see README)
  1_Pop_annu_compo_evol_depreg.xlsx     population on 1 January of each year, by département
  base-cc-serie-historique-2022.CSV     commune populations (D99_POP, P06_POP, P22_POP) and surface SUPERF (km²)
  grille_densite_2025_geo2025.xlsx      grille communale de densité 2025 (communes and départements)

Départements: density = population on 1 January of the election year / surface (sum of the commune surfaces,
2022 geography, same for both years). Main sample: the 96 metropolitan départements (`in_main_sample`). `region_code` = current (2016) region, from the INSEE
`Code REG-DEP` of the latest year, used for both years (metropolitan départements only).
`rural_population_share` = share of the population living in rural communes of the INSEE density grid (2022
population, used for both years).

Communes: the INSEE file uses a single recent commune geography (later than the 2022 election).
  * 2022 density = P22_POP / SUPERF
  * 2002 density = population_2002_interp / SUPERF, with population_2002_interp = D99_POP × (P06_POP / D99_POP)^(3/7),
    a geometric interpolation between the 1999 census and 2006, ≈ spring 2002. `population` keeps D99_POP.
  * 2002 communes that later merged into a *commune nouvelle* either have no INSEE code (unmatched) or match a larger
    2022 commune. The latter are flagged by registered / population outside [0.4, 1.2] (`possible_boundary_change`).
  * main_sample = metropolitan, INSEE match, finite log density, CENP available, no possible boundary change.
  * density_grid (dense urban / intermediate urban / rural) and density_grid_aav (rural split into periurban and
    non-periurban): INSEE density grid 2025, which classifies communes by how concentrated their population is on
    1 km² cells, not by population / total surface. Based on the 2022 population, used for both years.
  Excluded observations are kept in the files with their flags.

Outputs
  data/processed/coordination_density_departements.csv        one row per year × département
  data/processed/coordination_density_departements_wide.csv   2002–2022 comparison (ΔHHI, ΔCENP), metropolitan départements
  data/processed/coordination_density_communes_corrected.csv  one row per year × commune (+ one file per year)

Usage: python scripts/04_density.py [--force] [--level departements|communes]
"""
import argparse

import numpy as np
import pandas as pd

from svgeo.config import (ARRONDISSEMENTS, COMMUNE_DENSITY, COMMUNE_DENSITY_BY_YEAR, COMMUNE_INDICES,
                          DEPARTMENT_DENSITY, DEPARTMENT_DENSITY_WIDE, DEPARTMENT_INDICES, INSEE_COMMUNES,
                          DENSITY_GRID_AAV_LEVELS, DENSITY_GRID_LEVELS, INSEE_DENSITY_GRID, INSEE_POP_DEPARTEMENTS,
                          METRO_CODES, PLM, YEARS)
from svgeo.utils import norm_text, read_csv, report, show

DENSEST_DEPARTEMENTS = {"75", "92", "93", "94"}  # Paris and the petite couronne


# ======================= Départements =======================
def clean_code(value):
    """Excel cell -> code as text ('1175', '942A', '971'); None for empty cells."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def read_population_sheet(year):
    """Population on 1 January `year` by département, from the INSEE sheet named after the year.

    `Code REG-DEP` concatenates region and département codes for metropolitan départements ('1175' = region 11 +
    Paris 75, '942A' = region 94 + Corse-du-Sud); overseas départements have a plain 3-digit code ('971').
    Region rows, totals and footnotes are dropped.
    """
    raw = pd.read_excel(INSEE_POP_DEPARTEMENTS, sheet_name=str(year), header=None)
    # Locate the header row and the relevant columns from their labels (not from fixed positions)
    header_row = raw.apply(lambda row: row.astype(str).str.contains("Code REG-DEP").any(), axis=1).idxmax()
    header, sub_header = raw.loc[header_row].astype(str), raw.loc[header_row + 1].astype(str)
    code_col = header.index[header.str.contains("Code REG-DEP")][0]
    pop_col = header.index[header.str.strip().str.startswith("Population")][0]
    assert "1er janvier" in sub_header[pop_col] and "suivant" not in sub_header[pop_col], sub_header[pop_col]

    data = raw.loc[header_row + 2:, [0, code_col, pop_col]]
    data.columns = ["insee_name", "reg_dep_code", "population"]
    data["reg_dep_code"] = data["reg_dep_code"].map(clean_code)
    is_metro = data["reg_dep_code"].str.fullmatch(r"\d{2}(\d{2}|2[AB])", na=False)
    is_overseas = data["reg_dep_code"].str.fullmatch(r"97\d", na=False)
    data["department_code"] = np.where(is_metro, data["reg_dep_code"].str[2:],
                                       np.where(is_overseas, data["reg_dep_code"], None))
    data["region_code"] = np.where(is_metro, data["reg_dep_code"].str[:2], None)  # 2016 regions, metropolitan only
    deps = data[data["department_code"].notna()].copy()
    deps["population"] = pd.to_numeric(deps["population"], errors="raise")
    deps["insee_name"] = deps["insee_name"].astype(str).str.strip()
    deps.insert(0, "year", year)
    return deps[["year", "department_code", "region_code", "insee_name", "reg_dep_code", "population"]]


def department_surfaces():
    """Sum of commune surfaces by département; arrondissements excluded (otherwise Paris, Lyon, Marseille twice)."""
    communes = pd.read_csv(INSEE_COMMUNES, sep=";", usecols=["CODGEO", "SUPERF"], dtype={"CODGEO": str},
                           encoding="utf-8")
    communes["department_code"] = communes["CODGEO"].map(lambda c: c[:3] if c.startswith("97") else c[:2])
    communes["is_arrondissement"] = communes["CODGEO"].isin(ARRONDISSEMENTS)
    for code, prefix in {"75056": "751", "69123": "6938", "13055": "132"}.items():
        whole = communes.loc[communes["CODGEO"] == code, "SUPERF"]
        arr = communes.loc[communes["is_arrondissement"] & communes["CODGEO"].str.startswith(prefix), "SUPERF"]
        report(f"{PLM[code]}: whole commune present, surface = sum of its {len(arr)} arrondissements",
               len(whole) == 1 and np.isclose(whole.sum(), arr.sum()))
    return (communes[~communes["is_arrondissement"]]
            .groupby("department_code", as_index=False)
            .agg(surface_km2=("SUPERF", "sum"), n_communes=("CODGEO", "size")))


def departements():
    coord = read_csv(DEPARTMENT_INDICES)
    population = pd.concat([read_population_sheet(year) for year in YEARS], ignore_index=True)
    report("population: unique year × département", not population.duplicated(["year", "department_code"]).any())
    surface = department_surfaces()

    density = population.merge(surface[["department_code", "surface_km2"]], on="department_code",
                               how="left", validate="many_to_one", indicator=True)
    no_surface = density[density["_merge"] == "left_only"]
    report("every INSEE département has a surface", no_surface.empty, no_surface)
    density = density.drop(columns="_merge")
    # Each sheet codes the regions of its own year (the 2002 sheet: the 22 pre-2016 regions); use the current ones
    latest = density[density["year"] == max(YEARS)].set_index("department_code")["region_code"]
    density["region_code"] = density["department_code"].map(latest)
    density["density"] = density["population"] / density["surface_km2"]
    density["log_density"] = np.log(density["density"])

    # Merge key year × department_code; all territories of the election data are kept, flagged by in_main_sample
    merged = coord.merge(density[["year", "department_code", "region_code", "insee_name", "population", "surface_km2",
                                  "density", "log_density"]],
                         on=["year", "department_code"], how="outer", validate="one_to_one", indicator=True)
    merged["in_main_sample"] = merged["department_code"].isin(METRO_CODES)
    print("Unmatched — election data only:",
          merged.loc[merged["_merge"] == "left_only", ["year", "department_code"]].values.tolist())
    print("Unmatched — INSEE only (dropped):",
          merged.loc[merged["_merge"] == "right_only", ["year", "department_code"]].values.tolist())
    unmatched_metro = merged[merged["in_main_sample"] & (merged["_merge"] != "both")]
    assert unmatched_metro.empty, f"metropolitan départements not matched:\n{unmatched_metro[['year', 'department_code']]}"
    merged = merged[merged["_merge"] != "right_only"].drop(columns="_merge")
    rural = pd.read_excel(INSEE_DENSITY_GRID, sheet_name="Maille départementale", header=4, dtype={"DEP": str})
    rural = rural.rename(columns={"DEP": "department_code"}).assign(rural_population_share=lambda d: d["P_RURAL"] / 100)
    merged = merged.merge(rural[["department_code", "rural_population_share"]], on="department_code", how="left",
                          validate="many_to_one")
    merged = merged[[*coord.columns, "region_code", "population", "surface_km2", "density", "log_density",
                     "rural_population_share", "in_main_sample", "insee_name"]].sort_values(["year", "department_code"]).reset_index(drop=True)

    main = merged[merged["in_main_sample"]]
    for year in YEARS:
        codes = main.loc[main["year"] == year, "department_code"]
        report(f"{year}: exactly the 96 metropolitan départements", set(codes) == set(METRO_CODES) and len(codes) == 96)
        top = set(main[main["year"] == year].nlargest(len(DENSEST_DEPARTEMENTS), "density")["department_code"])
        report(f"{year}: the densest départements are {sorted(DENSEST_DEPARTEMENTS)}", top == DENSEST_DEPARTEMENTS,
               pd.Series(sorted(top), dtype=str))
    for col in ["population", "surface_km2"]:
        report(f"no metropolitan département missing {col}", main[col].notna().all())
    report("densities positive, log density finite", (main["density"] > 0).all() and np.isfinite(main["log_density"]).all())
    report("surface identical in 2002 and 2022", (main.groupby("department_code")["surface_km2"].nunique() == 1).all())
    report("no overseas territory in the main sample", main["department_type"].eq("metropole").all())
    report("rural population share in [0, 1] for every metropolitan département",
           main["rural_population_share"].between(0, 1).all())
    report("13 metropolitan regions, one per département", main["region_code"].notna().all()
           and main["region_code"].nunique() == 13 and (main.groupby("department_code")["region_code"].nunique() == 1).all())
    name_diff = main[main["department_name"].map(norm_text) != main["insee_name"].map(norm_text)]
    print(f"Name differences election / INSEE (diagnostic, spelling only expected): {len(name_diff)}")
    show(name_diff[["year", "department_code", "department_name", "insee_name"]])

    # Matched 2002–2022 comparison, one row per metropolitan département.
    # delta_HHI is the main cross-year comparison (HHI does not depend on the number of candidates K); delta_CENP is
    # kept as a supplementary result. delta_log_density = log change in population (same surface in both years).
    wide = main.pivot(index="department_code", columns="year",
                      values=["HHI", "CENP", "log_density", "density", "population"])
    wide.columns = [f"{var}_{year}" for var, year in wide.columns]
    wide = wide.reset_index()
    first = main.drop_duplicates("department_code").set_index("department_code")
    wide.insert(1, "department_name", wide["department_code"].map(first["department_name"]))
    wide["surface_km2"] = wide["department_code"].map(first["surface_km2"])
    wide["delta_CENP"] = wide["CENP_2022"] - wide["CENP_2002"]
    wide["delta_HHI"] = wide["HHI_2022"] - wide["HHI_2002"]
    wide["delta_log_density"] = wide["log_density_2022"] - wide["log_density_2002"]
    report("wide dataset: 96 départements, no missing value", len(wide) == 96 and wide.notna().all().all())
    return merged.drop(columns="insee_name"), wide


# ======================= Communes =======================
def load_insee_communes():
    """INSEE commune file without the arrondissement rows (the analysis unit is the whole commune)."""
    insee = pd.read_csv(INSEE_COMMUNES, sep=";", dtype={"CODGEO": str}, encoding="utf-8",
                        usecols=["CODGEO", "P22_POP", "P06_POP", "D99_POP", "SUPERF"])
    insee = insee[~insee["CODGEO"].isin(ARRONDISSEMENTS)].rename(columns={"CODGEO": "commune_code",
                                                                          "SUPERF": "surface_km2"})
    report("INSEE: commune codes unique", not insee["commune_code"].duplicated().any())
    report("INSEE: whole Paris, Lyon, Marseille present", set(PLM) <= set(insee["commune_code"]))
    return insee


def load_density_grid():
    """INSEE density grid 2025, one row per commune: density_grid (3 levels) and density_grid_aav (4 levels)."""
    grid = pd.read_excel(INSEE_DENSITY_GRID, sheet_name="Maille communale", header=4, dtype={"CODGEO": str})
    report("density grid: commune codes unique", grid["CODGEO"].is_unique)
    report("density grid: expected levels", set(grid["DENS"]) == set(DENSITY_GRID_LEVELS)
           and set(grid["DENS_AAV"]) == set(DENSITY_GRID_AAV_LEVELS))
    return pd.DataFrame({"commune_code": grid["CODGEO"], "density_grid": grid["DENS"].map(DENSITY_GRID_LEVELS),
                         "density_grid_aav": grid["DENS_AAV"].map(DENSITY_GRID_AAV_LEVELS)})


def commune_density(insee):
    """One row per year × commune: census population, 2002 interpolation, density population, density."""
    rows = []
    for year, pop_col in {2002: "D99_POP", 2022: "P22_POP"}.items():
        d = insee[["commune_code", "surface_km2", pop_col]].rename(columns={pop_col: "population"})
        d.insert(0, "year", year)
        if year == 2002:
            d["population_2002_interp"] = insee["D99_POP"] * (insee["P06_POP"] / insee["D99_POP"]) ** (3 / 7)
        rows.append(d)
    density = pd.concat(rows, ignore_index=True)
    is_2002 = density["year"] == 2002
    density["density_population_variable"] = np.where(is_2002, "population_2002_interp", "population")
    density["density_population"] = np.where(is_2002, density["population_2002_interp"], density["population"])
    density["density"] = density["density_population"] / density["surface_km2"]
    with np.errstate(divide="ignore"):
        density["log_density"] = np.log(density["density"])
    no_pop = density[~(density["density_population"] > 0)]
    print(f"Communes whose density population is 0 or missing (log density not finite): {len(no_pop)}")
    show(no_pop)
    return density


def communes():
    indices = read_csv(COMMUNE_INDICES)
    density = commune_density(load_insee_communes())

    merged = indices.merge(density, on=["year", "commune_code"], how="left", validate="one_to_one", indicator=True)
    merged["insee_match"] = merged["_merge"] == "both"
    merged = merged.drop(columns="_merge")
    merged["metropolitan"] = merged["department_code"].isin(METRO_CODES)
    merged = merged.merge(load_density_grid(), on="commune_code", how="left", validate="many_to_one")
    # Census population (D99_POP in 2002), not the density population: the sample rule does not depend on it
    merged["registered_to_population"] = merged["registered"] / merged["population"]
    merged["possible_boundary_change"] = merged["insee_match"] & ~merged["registered_to_population"].between(0.4, 1.2)
    merged["main_sample"] = (merged["metropolitan"] & merged["insee_match"] & np.isfinite(merged["log_density"])
                             & merged["CENP"].notna() & ~merged["possible_boundary_change"])

    # Checks
    v02 = merged[(merged["year"] == 2002) & merged["insee_match"] & (merged["population_2002_interp"] > 0)
                 & (merged["surface_km2"] > 0)]
    report("2002 density = population_2002_interp / surface_km2",
           (v02["density_population_variable"] == "population_2002_interp").all()
           and np.allclose(v02["density"], v02["population_2002_interp"] / v02["surface_km2"]))
    v22 = merged[(merged["year"] == 2022) & merged["insee_match"] & (merged["population"] > 0)]
    report("2022 density = population / surface_km2", np.allclose(v22["density"], v22["population"] / v22["surface_km2"]))
    report("no duplicated commune-year rows", not merged.duplicated(["year", "commune_code"]).any())
    main = merged[merged["main_sample"]]
    report("main sample: every commune has a density grid level", main["density_grid"].notna().all(),
           main.loc[main["density_grid"].isna(), ["year", "commune_code", "commune_name"]])
    report("main sample: population, surface, density positive and log density finite",
           (main["population"] > 0).all() and (main["surface_km2"] > 0).all() and np.isfinite(main["log_density"]).all())
    report("candidate totals consistent with expressed votes", merged["turnout_matches_sum"].all(),
           merged.loc[~merged["turnout_matches_sum"], ["year", "commune_code", "commune_name"]])
    for year in YEARS:
        unmatched = merged[(merged["year"] == year) & ~merged["insee_match"]]
        print(f"{year}: {len(unmatched)} election communes without INSEE match "
              f"({unmatched['metropolitan'].sum()} metropolitan) — kept, excluded from main_sample")
    print("Communes per year:")
    show(merged.groupby("year").agg(all=("commune_code", "size"), metropolitan=("metropolitan", "sum"),
                                    insee_match=("insee_match", "sum"),
                                    possible_boundary_change=("possible_boundary_change", "sum"),
                                    main_sample=("main_sample", "sum")))
    show(merged.loc[merged["commune_code"].isin(PLM), ["year", "commune_code", "commune_name", "population",
                                                        "surface_km2", "density", "HHI", "CENP", "main_sample"]])
    return merged.sort_values(["year", "commune_code"])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild even if the outputs exist")
    parser.add_argument("--level", choices=["departements", "communes"], help="only one level (default: both)")
    args = parser.parse_args()

    if args.level in (None, "departements"):
        if DEPARTMENT_DENSITY.exists() and DEPARTMENT_DENSITY_WIDE.exists() and not args.force:
            print(f"{DEPARTMENT_DENSITY.name} exists — skipped (use --force to rebuild)")
        else:
            print("\n===== départements =====")
            long, wide = departements()
            long.to_csv(DEPARTMENT_DENSITY, index=False, encoding="utf-8")
            wide.to_csv(DEPARTMENT_DENSITY_WIDE, index=False, encoding="utf-8")
            print(f"Saved {len(long)} rows to {DEPARTMENT_DENSITY} and {len(wide)} rows to {DEPARTMENT_DENSITY_WIDE}")

    if args.level in (None, "communes"):
        if COMMUNE_DENSITY.exists() and not args.force:
            print(f"{COMMUNE_DENSITY.name} exists — skipped (use --force to rebuild)")
        else:
            print("\n===== communes =====")
            out = communes()
            out.to_csv(COMMUNE_DENSITY, index=False, encoding="utf-8")
            for year in YEARS:
                out[out["year"] == year].to_csv(COMMUNE_DENSITY_BY_YEAR[year], index=False, encoding="utf-8")
            print(f"Saved {len(out)} rows to {COMMUNE_DENSITY} ({out['main_sample'].sum()} in the main samples)")


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    main()
