"""Interactive département explorer — builds a standalone docs/index.html for GitHub Pages.

The page is self-contained: Plotly.js, the département geometry and all values are embedded, so it opens from disk
and needs no server. Everything shown is computed here from the processed data (nothing is hard-coded):
HHI (vote concentration) by year and ΔHHI — the main cross-year layer —, CENP by year and ΔCENP (supplementary
normalised measure), ranks, the descriptive 2022 regression CENP ~ log(density) and its residuals, cliff metrics and,
if available, the candidates' first-round vote shares and the national HHI.

Inputs
  data/processed/coordination_density_departements.csv   indices + density, one row per year × département (step 4)
  data/processed/presidential_departments_2002_2022.csv  candidate votes by département (step 1) — optional
  data/raw/geography/departements-100m.geojson           département boundaries (downloaded by hand)
Output
  docs/index.html

Usage: python scripts/build_department_explorer.py
"""
import json
import sys

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
import statsmodels.formula.api as smf
from matplotlib.colors import to_hex
from matplotlib.ticker import MaxNLocator
from plotly.offline import get_plotlyjs
from shapely.geometry import mapping

from svgeo.config import DEPARTMENT_BOUNDARIES, DEPARTMENT_DENSITY, DEPARTMENT_RESULTS, METRO_CODES, ROOT, YEARS
from svgeo.utils import read_csv

# ---- Paths ----
GEOMETRY_PATH = DEPARTMENT_BOUNDARIES
GEOMETRY_CODE_COLUMN = "code"  # property of the GeoJSON holding the département code ('01', '2A', …)
OUTPUT_HTML = ROOT / "docs" / "index.html"
REPOSITORY_URL = "https://github.com/clarasalas/strategic-voting-geo-2RS"

# ---- Map geometry ----
SIMPLIFY_TOLERANCE = 0.002  # degrees (≈ 150–200 m): invisible at page scale, keeps the page small
COORDINATE_DECIMALS = 4

# ---- Colours ----
# Sequential maps run light (low) -> dark (high): HHI and CENP use rocket_r like notebooks/maps_departements.ipynb,
# density uses mako_r. Change and residual maps use Spectral_r centred on zero, as in the static figures (positive =
# more concentrated on the dark red side).
N_COLOR_STOPS = 11
CENP_PALETTE, DENSITY_PALETTE, DIVERGING_PALETTE = "rocket_r", "mako_r", "Spectral_r"
CLIFF_CATEGORIES = ["1", "2", "3", "4", "5+"]
# Cliff location: Spectral_r sampled cool (1) -> warm (5+), skipping the near-white middle of the scale
CLIFF_SPECTRAL_POSITIONS = [0.0, 0.2, 0.65, 0.8, 1.0]
DENSITY_TICKS = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]

INDICATOR_COLUMNS = ["year", "department_code", "department_name", "HHI", "CENP", "cliff_location", "cliff_magnitude",
                     "cliff_ratio", "density", "log_density"]
CANDIDATE_COLUMNS = ["year", "dep_code", "candidate_clean", "votes"]


def warn(message):
    print(f"WARNING: {message}", file=sys.stderr)


def require_columns(df, columns, name):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise KeyError(f"{name}: required columns missing: {missing}")


def require_file(path, what):
    if not path.exists():
        raise FileNotFoundError(f"{what} not found at {path}")


# ======================= Data =======================
def load_department_indicators():
    """One row per metropolitan département: HHI, CENP, cliff metrics and density for each year (`<var>_<year>`)."""
    require_file(DEPARTMENT_DENSITY, "Département indices/density file (run scripts/01–04)")
    long = read_csv(DEPARTMENT_DENSITY)
    require_columns(long, INDICATOR_COLUMNS, DEPARTMENT_DENSITY.name)
    long = long[long["department_code"].isin(METRO_CODES) & long["year"].isin(YEARS)]
    if long.duplicated(["year", "department_code"]).any():
        raise ValueError(f"{DEPARTMENT_DENSITY.name}: duplicated year × département rows")
    for year in YEARS:
        missing = sorted(set(METRO_CODES) - set(long.loc[long["year"] == year, "department_code"]))
        if missing:
            warn(f"{year}: {len(missing)} metropolitan départements missing from the data: {missing}")

    values = ["HHI", "CENP", "cliff_location", "cliff_magnitude", "cliff_ratio", "density", "log_density"]
    dep = long.pivot(index="department_code", columns="year", values=values)
    dep.columns = [f"{var.lower()}_{year}" for var, year in dep.columns]
    names = long.sort_values("year").groupby("department_code")["department_name"].last()
    return dep.join(names.rename("name")).reset_index().rename(columns={"department_code": "code"})


def add_derived_measures(dep):
    """ΔHHI, ΔCENP, ranks (1 = most concentrated), 2022 descriptive regression CENP ~ log(density), cliff categories.

    Within one election K is fixed and CENP is increasing in HHI, so the rank is the same with either measure.
    """
    dep = dep.copy()
    first, last = YEARS[0], YEARS[-1]
    dep["delta_hhi"] = dep[f"hhi_{last}"] - dep[f"hhi_{first}"]
    dep["delta_cenp"] = dep[f"cenp_{last}"] - dep[f"cenp_{first}"]
    for year in YEARS:
        dep[f"rank_{year}"] = dep[f"hhi_{year}"].rank(ascending=False, method="min").astype("Int64")
        dep[f"cliff_group_{year}"] = pd.cut(dep[f"cliff_location_{year}"], bins=[0.5, 1.5, 2.5, 3.5, 4.5, np.inf],
                                            labels=CLIFF_CATEGORIES).astype(object)
        dep[f"cliff_location_{year}"] = dep[f"cliff_location_{year}"].astype("Int64")

    sample = dep.dropna(subset=[f"cenp_{last}", f"log_density_{last}"])
    fit = smf.ols(f"cenp_{last} ~ log_density_{last}", data=sample).fit()
    # Fitted values from the coefficients, so départements without a density keep a missing value (not dropped)
    dep[f"fitted_{last}"] = fit.params["Intercept"] + fit.params[f"log_density_{last}"] * dep[f"log_density_{last}"]
    dep[f"residual_{last}"] = dep[f"cenp_{last}"] - dep[f"fitted_{last}"]
    regression = {"year": last, "n": int(fit.nobs), "intercept": fit.params["Intercept"],
                  "slope": fit.params[f"log_density_{last}"], "r2": fit.rsquared}
    print(f"{last} regression CENP ~ log(density): slope {regression['slope']:.4f}, R² {regression['r2']:.3f}, "
          f"n = {regression['n']}")
    return dep, regression


def load_candidate_shares():
    """({code: {year: [{name, share}], sorted by share}}, {year: national HHI}), or (None, None) if the
    candidate-level file is unavailable. The national HHI uses candidate votes summed over the metropolitan
    départements, i.e. the concentration of the national result (not the mean across départements)."""
    if not DEPARTMENT_RESULTS.exists():
        warn(f"candidate-level results not found at {DEPARTMENT_RESULTS} — the vote-share chart is left out")
        return None, None
    results = read_csv(DEPARTMENT_RESULTS)
    try:
        require_columns(results, CANDIDATE_COLUMNS, DEPARTMENT_RESULTS.name)
    except KeyError as error:
        warn(f"{error} — the vote-share chart is left out")
        return None, None
    if "round" in results:
        results = results[results["round"] == 1]
    results = results[results["dep_code"].isin(METRO_CODES) & results["year"].isin(YEARS)]
    votes = results.groupby(["year", "dep_code", "candidate_clean"], as_index=False)["votes"].sum()
    votes["share"] = votes["votes"] / votes.groupby(["year", "dep_code"])["votes"].transform("sum")
    votes["name"] = votes["candidate_clean"].str.title()
    national = votes.groupby(["year", "candidate_clean"])["votes"].sum()
    national_hhi = ((national / national.groupby(level="year").transform("sum")) ** 2).groupby(level="year").sum()

    shares = {}
    for (year, code), group in votes.sort_values("share", ascending=False).groupby(["year", "dep_code"], sort=False):
        shares.setdefault(code, {})[str(year)] = [{"name": n, "share": round(float(s), 5)}
                                                  for n, s in zip(group["name"], group["share"])]
    return shares, {str(year): float(v) for year, v in national_hhi.items()}


def load_geometry(codes):
    """Simplified metropolitan GeoJSON (WGS84) with a `code` property; unmatched codes are reported."""
    require_file(GEOMETRY_PATH, "Département boundary file")
    geo = gpd.read_file(GEOMETRY_PATH)
    require_columns(geo, [GEOMETRY_CODE_COLUMN], GEOMETRY_PATH.name)
    geo = geo[[GEOMETRY_CODE_COLUMN, "geometry"]].rename(columns={GEOMETRY_CODE_COLUMN: "code"})
    geo["code"] = geo["code"].astype(str).str.strip().str.upper()
    geo["code"] = geo["code"].where(~geo["code"].str.fullmatch(r"\d"), geo["code"].str.zfill(2))
    if geo["code"].duplicated().any():
        geo = geo.dissolve(by="code", as_index=False)
    geo = geo[geo["code"].isin(METRO_CODES)]
    if geo.crs is None:
        raise ValueError(f"{GEOMETRY_PATH.name} has no CRS")
    geo = geo.to_crs("EPSG:4326")

    no_geometry = sorted(set(codes) - set(geo["code"]))
    no_data = sorted(set(geo["code"]) - set(codes))
    if no_geometry:
        warn(f"départements without geometry (not drawn): {no_geometry}")
    if no_data:
        warn(f"geometries without data (drawn in grey): {no_data}")
    if geo.empty:
        raise RuntimeError("no metropolitan département geometry matched")

    simplified = np.asarray(geo.geometry.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True))
    geometry = [_polygonal(shape) for shape in shapely.set_precision(simplified, 10 ** -COORDINATE_DECIMALS)]
    features = [{"type": "Feature", "properties": {"code": code},
                 "geometry": _rounded_geometry(mapping(shape))}
                for code, shape in zip(geo["code"], geometry) if not shape.is_empty]
    lost = sorted(set(geo["code"]) - {f["properties"]["code"] for f in features})
    if lost:
        warn(f"geometry empty after simplification (not drawn): {lost} — lower SIMPLIFY_TOLERANCE")
    return {"type": "FeatureCollection", "features": features}


def _polygonal(shape):
    """Polygon / MultiPolygon only: snapping to the coordinate grid can leave stray lines or points."""
    if shape.geom_type in ("Polygon", "MultiPolygon") or shape.is_empty:
        return shape
    parts = [g for g in getattr(shape, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def _rounded_geometry(geometry):
    def rounded(coords):
        if isinstance(coords[0], (int, float)):
            return [round(float(x), COORDINATE_DECIMALS) for x in coords]
        return [rounded(c) for c in coords]
    return {"type": geometry["type"], "coordinates": rounded(geometry["coordinates"])}


# ======================= Metrics and colours =======================
def colormap(name):
    """rocket / mako (and their _r versions) from seaborn when installed, a close matplotlib map otherwise."""
    base, reverse = (name[:-2], "_r") if name.endswith("_r") else (name, "")
    fallback = {"rocket": "magma", "mako": "viridis"}
    if base in fallback:
        try:
            import seaborn as sns
            return sns.color_palette(name, as_cmap=True)
        except ImportError:
            warn(f"seaborn not installed — '{fallback[base] + reverse}' used instead of '{name}'")
            return plt.get_cmap(fallback[base] + reverse)
    return plt.get_cmap(name)


def colorscale(name):
    cmap = colormap(name)
    return [[round(p, 4), to_hex(cmap(p))] for p in np.linspace(0, 1, N_COLOR_STOPS)]


def signed_label(value, decimals=2):
    if abs(value) < 0.5 * 10 ** -decimals:
        return "0"
    return f"{value:+.{decimals}f}".replace("-", "−")


def continuous_metric(key, label, field, palette, zmin, zmax, legend, short, value_format, diverging=False,
                      ends=None, ticks=None, display_field=None):
    if diverging:
        limit = max(abs(zmin), abs(zmax))
        zmin, zmax = -limit, limit
    if ticks is None:
        values = [t for t in MaxNLocator(nbins=5, symmetric=diverging).tick_values(zmin, zmax)
                  if zmin - 1e-9 <= t <= zmax + 1e-9]
        ticks = [{"value": float(t), "label": signed_label(t) if diverging else f"{t:.2f}"} for t in values]
    return {"key": key, "label": label, "field": field, "display_field": display_field or field,
            "kind": "continuous", "colorscale": colorscale(palette), "zmin": float(zmin), "zmax": float(zmax),
            "ticks": ticks, "legend": legend, "short": short, "format": value_format, "ends": ends}


def categorical_metric(key, label, field, legend, short, observed):
    cmap = plt.get_cmap(DIVERGING_PALETTE)
    colors = {cat: to_hex(cmap(pos)) for cat, pos in zip(CLIFF_CATEGORIES, CLIFF_SPECTRAL_POSITIONS)}
    n = len(CLIFF_CATEGORIES)
    scale = []
    for i, cat in enumerate(CLIFF_CATEGORIES):  # one flat band per category; z = index + 0.5
        scale += [[i / n, colors[cat]], [(i + 1) / n, colors[cat]]]
    return {"key": key, "label": label, "field": field, "display_field": field, "kind": "categorical",
            "categories": CLIFF_CATEGORIES, "legend_categories": [c for c in CLIFF_CATEGORIES if c in observed],
            "colors": colors, "colorscale": scale, "zmin": 0, "zmax": n, "legend": legend, "short": short,
            "format": "category"}


def build_metrics(dep, regression):
    first, last = YEARS[0], YEARS[-1]
    hhi = pd.concat([dep[f"hhi_{y}"] for y in YEARS]).dropna()
    cenp = pd.concat([dep[f"cenp_{y}"] for y in YEARS]).dropna()
    observed_cliff = set(pd.concat([dep[f"cliff_group_{y}"] for y in YEARS]).dropna())

    # Main cross-year layer first (it is the default map)
    delta_hhi = dep["delta_hhi"].dropna()
    metrics = [continuous_metric("delta_hhi", "Change in HHI", "delta_hhi", DIVERGING_PALETTE, delta_hhi.min(),
                                 delta_hhi.max(), f"Change in vote concentration (HHI), {last} − {first}",
                                 "Change in HHI", "signed", diverging=True,
                                 ends=[f"Less concentrated in {last}", f"More concentrated in {last}"])]
    metrics += [continuous_metric(f"hhi_{y}", f"HHI — {y}", f"hhi_{y}", CENP_PALETTE, hhi.min(), hhi.max(),
                                  f"Vote concentration (HHI), {y} · higher = more concentrated · same scale for "
                                  f"{first} and {last}", f"HHI {y}", "fixed")
                for y in YEARS]
    metrics += [continuous_metric(f"cenp_{y}", f"CENP — {y}", f"cenp_{y}", CENP_PALETTE, cenp.min(), cenp.max(),
                                  f"CENP, {y} · supplementary normalised measure, depends on the number of candidates",
                                  f"CENP {y}", "fixed")
                for y in YEARS]
    delta = dep["delta_cenp"].dropna()
    # key "delta" kept so that existing links (#metric=delta) still open the CENP change map
    metrics.append(continuous_metric("delta", "Change in CENP", "delta_cenp", DIVERGING_PALETTE, delta.min(), delta.max(),
                                     f"Change in CENP, {last} − {first} · supplementary: CENP depends on the number "
                                     f"of candidates, so ΔHHI is the main comparison", "Change in CENP", "signed",
                                     diverging=True, ends=[f"Lower in {last}", f"Higher in {last}"]))
    log_density = dep[f"log_density_{last}"].dropna()
    lo, hi = log_density.min(), log_density.max()
    density_ticks = [{"value": float(np.log(v)), "label": f"{v:,}"} for v in DENSITY_TICKS
                     if lo <= np.log(v) <= hi]
    metrics.append(continuous_metric("density", f"Population density — {last}", f"log_density_{last}", DENSITY_PALETTE, lo, hi,
                                     f"Population density, {last} · inhabitants per km², log scale",
                                     f"Density {last}", "density", ticks=density_ticks,
                                     display_field=f"density_{last}"))
    residual = dep[f"residual_{last}"].dropna()
    metrics.append(continuous_metric("residual", f"Residual CENP — {last}", f"residual_{last}", DIVERGING_PALETTE,
                                     residual.min(), residual.max(),
                                     f"Residual CENP, {last}: observed − predicted from log density (descriptive)",
                                     f"Residual CENP {last}", "signed", diverging=True,
                                     ends=["Less concentrated than predicted by density",
                                           "More concentrated than predicted by density"]))
    for y in YEARS:
        metrics.append(categorical_metric(f"cliff_{y}", f"Cliff location — {y}", f"cliff_group_{y}",
                                          f"Cliff location, {y} · rank after which the largest vote-share drop occurs",
                                          f"Cliff location {y}", observed_cliff))
    return metrics


def build_summary(dep, regression, national_hhi):
    first, last = YEARS[0], YEARS[-1]
    hhi = pd.concat([dep[f"hhi_{y}"] for y in YEARS]).dropna()
    cenp = pd.concat([dep[f"cenp_{y}"] for y in YEARS]).dropna()
    delta = dep["delta_cenp"].dropna()
    delta_hhi = dep["delta_hhi"].dropna()
    return {"n_departments": int(len(dep)), "n_delta": int(len(delta)), "n_increase": int((delta > 0).sum()),
            "n_delta_hhi": int(len(delta_hhi)), "n_increase_hhi": int((delta_hhi > 0).sum()),
            "mean_hhi": {str(y): float(dep[f"hhi_{y}"].mean()) for y in YEARS}, "national_hhi": national_hhi,
            "hhi_min": float(hhi.min()), "hhi_max": float(hhi.max()),
            "mean_cenp": {str(y): float(dep[f"cenp_{y}"].mean()) for y in YEARS},
            "cenp_min": float(cenp.min()), "cenp_max": float(cenp.max()),
            "regression": {k: (float(v) if isinstance(v, (float, np.floating)) else v) for k, v in regression.items()},
            "first_year": first, "last_year": last}


# ======================= Page =======================
def build_page(payload):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    return (PAGE_TEMPLATE.replace("/*__PLOTLY_JS__*/", get_plotlyjs())
            .replace("/*__DATA__*/null", data)
            .replace("__REPOSITORY_URL__", REPOSITORY_URL))


def main():
    dep = load_department_indicators()
    dep, regression = add_derived_measures(dep)
    candidates, national_hhi = load_candidate_shares()
    if candidates is not None:
        no_candidates = sorted(set(dep["code"]) - set(candidates))
        if no_candidates:
            warn(f"no candidate results for: {no_candidates}")
    geojson = load_geometry(dep["code"])

    columns = ["code", "name", "delta_hhi", "delta_cenp", f"fitted_{YEARS[-1]}", f"residual_{YEARS[-1]}"]
    for year in YEARS:
        columns += [f"hhi_{year}", f"cenp_{year}", f"rank_{year}", f"density_{year}", f"log_density_{year}",
                    f"cliff_location_{year}", f"cliff_group_{year}", f"cliff_magnitude_{year}", f"cliff_ratio_{year}"]
    records = json.loads(dep[columns].sort_values("name").to_json(orient="records", double_precision=6))

    payload = {"years": YEARS, "departments": records, "candidates": candidates, "geojson": geojson,
               "metrics": build_metrics(dep, regression), "summary": build_summary(dep, regression, national_hhi)}
    OUTPUT_HTML.parent.mkdir(exist_ok=True)
    OUTPUT_HTML.write_text(build_page(payload), encoding="utf-8")
    size_mb = OUTPUT_HTML.stat().st_size / 1e6
    print(f"Saved {OUTPUT_HTML.relative_to(ROOT)} ({size_mb:.1f} MB, {len(records)} départements, "
          f"{len(geojson['features'])} geometries, vote shares {'included' if candidates else 'not available'})")


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vote concentration in France</title>
<meta name="description" content="Interactive map of first-round vote concentration (HHI, with CENP as a supplementary measure) by département, French presidential elections 2002 and 2022.">
<style>
  :root {
    --ink: #1a1a1a; --muted: #5f6368; --faint: #8a8f94; --rule: #e6e6e3; --soft: #f5f5f2;
    --dark-bar: #3d3d3d; --light-bar: #cfcfca;
    --font: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: #fff; color: var(--ink); font-family: var(--font); }
  body { -webkit-font-smoothing: antialiased; line-height: 1.45; }
  .page { max-width: 1320px; margin: 0 auto; padding: 36px 28px 48px; }
  header h1 { font-size: clamp(26px, 3.2vw, 36px); line-height: 1.15; letter-spacing: -0.015em; margin: 0 0 10px; }
  header .subtitle { font-size: 18px; color: var(--muted); margin: 0 0 8px; max-width: 760px; }
  header .note { font-size: 14px; color: var(--faint); margin: 0; max-width: 760px; }

  .layout { display: grid; grid-template-columns: minmax(0, 2.1fr) minmax(320px, 1fr); gap: 36px;
            margin-top: 26px; padding-top: 22px; border-top: 1px solid var(--rule); }
  .eyebrow { font-size: 12px; font-weight: 600; letter-spacing: .07em; text-transform: uppercase; color: var(--faint); }

  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 4px; }
  .chip { font: inherit; font-size: 13px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--rule);
          background: #fff; color: var(--ink); cursor: pointer; transition: background .15s, border-color .15s; }
  .chip:hover { border-color: #bdbdb8; }
  .chip[aria-checked="true"] { background: var(--ink); border-color: var(--ink); color: #fff; }
  .chip:focus-visible, select:focus-visible, .toggle button:focus-visible, .clear:focus-visible {
    outline: 2px solid #2b6cb0; outline-offset: 2px; }

  #map { width: 100%; height: min(74vh, 700px); }
  .legend { max-width: 460px; margin: 4px auto 0; font-size: 12px; color: var(--muted); }
  .legend-title { text-align: center; margin-bottom: 6px; color: var(--ink); font-size: 13px; }
  .legend .bar { height: 10px; border-radius: 2px; opacity: .9; }
  .legend .ticks { position: relative; height: 18px; margin-top: 3px; }
  .legend .ticks span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
  .legend .ends { display: flex; justify-content: space-between; gap: 12px; margin-top: 2px; }
  .legend .swatches { display: flex; justify-content: center; flex-wrap: wrap; gap: 6px 16px; }
  .legend .swatch { display: inline-flex; align-items: center; gap: 6px; color: var(--ink); }
  .legend .swatch i { width: 14px; height: 14px; border-radius: 2px; opacity: .9; display: inline-block; }

  aside { border-left: 1px solid var(--rule); padding-left: 30px; min-width: 0; }
  .finder { display: flex; flex-direction: column; gap: 6px; padding-bottom: 16px; border-bottom: 1px solid var(--rule); }
  .finder select { font: inherit; font-size: 14px; padding: 7px 10px; border: 1px solid var(--rule); border-radius: 6px;
                   background: #fff; color: var(--ink); }
  .panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin: 18px 0 4px; }
  .panel-head h2 { font-size: 26px; line-height: 1.15; margin: 4px 0 0; letter-spacing: -0.01em; }
  .clear { font: inherit; font-size: 12px; color: var(--muted); background: none; border: 1px solid var(--rule);
           border-radius: 999px; padding: 4px 10px; cursor: pointer; white-space: nowrap; }
  section.block { padding: 16px 0; border-bottom: 1px solid var(--rule); }
  section.block:last-child { border-bottom: none; }
  section.block h3 { margin: 0 0 10px; font-size: 12px; font-weight: 600; letter-spacing: .07em; text-transform: uppercase;
                     color: var(--faint); }
  .stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
  .stats.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat .label { font-size: 12px; color: var(--muted); }
  .stat .value { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
  .stat .value.small { font-size: 17px; }
  .small { font-size: 13px; color: var(--muted); margin: 10px 0 0; }
  .verdict { display: inline-block; margin-top: 12px; font-size: 13px; font-weight: 600; padding: 4px 10px; border-radius: 4px;
             background: var(--soft); }
  .prompt { margin: 18px 0 0; padding: 14px 16px; background: var(--soft); border-radius: 6px; font-size: 14px; }
  svg.dumbbell { width: 100%; height: auto; display: block; margin-top: 12px; }
  .caption { font-size: 12px; color: var(--faint); margin-top: 2px; }

  table.mini { width: 100%; border-collapse: collapse; font-size: 14px; font-variant-numeric: tabular-nums; }
  table.mini th, table.mini td { text-align: right; padding: 5px 0; border-bottom: 1px solid var(--rule); }
  table.mini th:first-child, table.mini td:first-child { text-align: left; }
  table.mini thead th { font-size: 12px; font-weight: 500; color: var(--muted); }
  table.mini tbody tr:last-child td, table.mini tbody tr:last-child th { border-bottom: none; }

  .toggle { display: inline-flex; border: 1px solid var(--rule); border-radius: 999px; padding: 2px; margin-bottom: 10px; }
  .toggle button { font: inherit; font-size: 12px; border: none; background: none; padding: 4px 12px; border-radius: 999px;
                   cursor: pointer; color: var(--muted); }
  .toggle button[aria-pressed="true"] { background: var(--ink); color: #fff; }
  .votes { display: grid; gap: 3px; }
  .vrow { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr) 46px; gap: 8px; align-items: center;
          font-size: 13px; }
  .vname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .vtrack { height: 10px; }
  .vbar { display: block; height: 100%; background: var(--light-bar); border-radius: 1px; }
  .vrow.above .vbar { background: var(--dark-bar); }
  .vpct { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }
  .vcliff { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--muted); margin: 3px 0; }
  .vcliff::before, .vcliff::after { content: ""; flex: 1; border-top: 1px dashed #9a9a95; }

  footer { margin-top: 36px; padding-top: 16px; border-top: 1px solid var(--rule); font-size: 12px; color: var(--faint); }
  footer p { margin: 4px 0; max-width: 900px; }
  footer a { color: var(--muted); }

  @media (max-width: 920px) {
    .page { padding: 24px 16px 40px; }
    .layout { grid-template-columns: 1fr; gap: 20px; }
    aside { border-left: none; padding-left: 0; border-top: 1px solid var(--rule); padding-top: 18px; }
    #map { height: 88vw; max-height: 560px; }
  }
</style>
</head>
<body>
<div class="page">
  <header>
    <h1>Geography of electoral coordination in France</h1>
    <p class="subtitle">Explore how concentrated the first-round vote was across départements in 2002 and 2022.</p>
    <p class="note">HHI measures vote concentration: higher values mean the vote was more concentrated on fewer
      candidates. It is the main measure for comparing the two elections. CENP is a supplementary normalised measure.
      Neither is, on its own, evidence of strategic voting.</p>
  </header>

  <div class="layout">
    <main>
      <div class="eyebrow" id="metric-label">Map</div>
      <div class="chips" id="chips" role="radiogroup" aria-label="Map metric"></div>
      <div id="map" role="img" aria-label="Map of metropolitan France by département"></div>
      <div class="legend" id="legend"></div>
    </main>
    <aside>
      <div class="finder">
        <label class="eyebrow" for="dep-select">Find a département</label>
        <select id="dep-select"></select>
      </div>
      <div id="panel" aria-live="polite"></div>
    </aside>
  </div>

  <footer>
    <p>Sources: Ministère de l'Intérieur (first-round results of the 2002 and 2022 presidential elections), INSEE
      (population, surface). 96 metropolitan départements; overseas départements are not shown.</p>
    <p>HHI = Σ (vote share)², from 1/K (votes spread evenly over the K candidates) to 1 (all votes on one candidate);
      an unchanged vote gives an unchanged HHI whatever the number of candidates. ENP = 1 / HHI is the effective number
      of candidates. CENP = (K − ENP) / (K − 1), with K the number of candidates nationally (16 in 2002, 12 in 2022),
      depends directly on K, so HHI is used to compare the two elections. Changes in concentration can reflect candidate
      supply, preferences, campaigns or strategic voting. All relationships with density are descriptive associations,
      not causal effects.</p>
    <p>Code and method: <a href="__REPOSITORY_URL__">__REPOSITORY_URL__</a></p>
  </footer>
</div>

<script>/*__PLOTLY_JS__*/</script>
<script>
(function () {
  "use strict";
  const DATA = /*__DATA__*/null;
  const METRICS = DATA.metrics;
  const METRIC = Object.fromEntries(METRICS.map(m => [m.key, m]));
  const DEPS = DATA.departments;
  const BY_CODE = new Map(DEPS.map(d => [d.code, d]));
  const S = DATA.summary;
  const FIRST = String(S.first_year), LAST = String(S.last_year);
  const MISSING_COLOR = "#e6e6e3";
  const MINUS = "−";

  // Plotly's geo subplot downloads a world basemap from cdn.plot.ly whenever a trace has a locationmode, which
  // custom-GeoJSON choropleths always have. Every basemap layer is hidden here, so an empty topology is registered
  // under the names Plotly looks for: no request is made and the page works offline.
  window.PlotlyGeoAssets = window.PlotlyGeoAssets || {};
  window.PlotlyGeoAssets.topojson = window.PlotlyGeoAssets.topojson || {};
  ["world_110m", "world_50m"].forEach(name => {
    if (!window.PlotlyGeoAssets.topojson[name]) {
      window.PlotlyGeoAssets.topojson[name] = { type: "Topology", objects: {}, arcs: [] };
    }
  });

  const state = { metric: METRICS[0].key, selected: null, voteYear: LAST };

  // ---------- formatting ----------
  const isNum = v => typeof v === "number" && Number.isFinite(v);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fixed = (v, n = 3) => isNum(v) ? v.toFixed(n) : "—";
  const signed = (v, n = 3) => {
    if (!isNum(v)) return "—";
    const s = Math.abs(v).toFixed(n);
    return Number(s) === 0 ? (0).toFixed(n) : (v > 0 ? "+" : MINUS) + s;
  };
  const intFmt = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
  const densityFmt = v => isNum(v) ? (v >= 10 ? intFmt.format(v) : v.toFixed(1)) : "—";
  const pct = v => isNum(v) ? (100 * v).toFixed(1) + "%" : "—";
  const ordinal = n => n + (n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th"));
  const cliffText = cat => cat == null ? "—" : cat === "5+" ? "after the 5th candidate or later" : "after the " + ordinal(Number(cat)) + " candidate";

  function formatMetric(m, d) {
    const v = d[m.display_field];
    if (m.format === "signed") return signed(v);
    if (m.format === "density") return isNum(v) ? densityFmt(v) + " inh./km²" : "—";
    if (m.format === "category") return cliffText(v);
    return fixed(v);
  }
  const hasValue = (m, d) => m.kind === "categorical" ? d[m.field] != null : isNum(d[m.field]);

  // ---------- map ----------
  const mapEl = document.getElementById("map");
  const LAYOUT = {
    geo: {
      fitbounds: "locations", visible: false, showframe: false, bgcolor: "rgba(0,0,0,0)",
      projection: { type: "conic conformal", parallels: [44, 49], rotation: { lon: 3 } }
    },
    margin: { l: 0, r: 0, t: 0, b: 0 }, paper_bgcolor: "rgba(0,0,0,0)", dragmode: false, showlegend: false,
    hoverlabel: { bgcolor: "#fff", bordercolor: "#d6d6d2", font: { family: getComputedStyle(document.body).fontFamily, size: 13, color: "#1a1a1a" }, align: "left" }
  };
  const CONFIG = { displayModeBar: false, responsive: true, scrollZoom: false, doubleClick: false };
  const BASE = { type: "choropleth", geojson: DATA.geojson, featureidkey: "properties.code", showscale: false };

  function hoverText(d, m) {
    return "<b>" + esc(d.name) + "</b> <span style='color:#8a8f94'>" + d.code + "</span><br>" +
      esc(m.short) + ": <b>" + formatMetric(m, d) + "</b><br>" +
      "<span style='color:#5f6368'>Vote concentration (HHI) " + fixed(d["hhi_" + FIRST]) + " (" + FIRST + ") → " +
      fixed(d["hhi_" + LAST]) + " (" + LAST + ")<br>CENP (supplementary) " + fixed(d["cenp_" + FIRST]) + " → " +
      fixed(d["cenp_" + LAST]) + "</span>";
  }

  function mapTraces() {
    const m = METRIC[state.metric];
    const shown = DEPS.filter(d => hasValue(m, d));
    const missing = DEPS.filter(d => !hasValue(m, d));
    const traces = [];
    if (missing.length) {  // grey, still hoverable and clickable
      traces.push(Object.assign({}, BASE, {
        locations: missing.map(d => d.code), z: missing.map(() => 0), zmin: 0, zmax: 1,
        colorscale: [[0, MISSING_COLOR], [1, MISSING_COLOR]], marker: { line: { color: "#fff", width: 0.6 } },
        text: missing.map(d => hoverText(d, m) + "<br><i>No value for this map</i>"), hovertemplate: "%{text}<extra></extra>"
      }));
    }
    traces.push(Object.assign({}, BASE, {
      locations: shown.map(d => d.code),
      z: shown.map(d => m.kind === "categorical" ? m.categories.indexOf(d[m.field]) + 0.5 : d[m.field]),
      zmin: m.zmin, zmax: m.zmax, colorscale: m.colorscale,
      marker: { opacity: 0.9, line: { color: "#fff", width: 0.6 } },
      text: shown.map(d => hoverText(d, m)), hovertemplate: "%{text}<extra></extra>"
    }));
    if (state.selected && BY_CODE.has(state.selected)) {  // outline drawn on top, ignored by hover and clicks
      traces.push(Object.assign({}, BASE, {
        locations: [state.selected], z: [0], zmin: 0, zmax: 1,
        colorscale: [[0, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0)"]],
        marker: { line: { color: "#111", width: 2.4 } }, hoverinfo: "skip"
      }));
    }
    return traces;
  }

  // ---------- legend and selector ----------
  function renderLegend() {
    const m = METRIC[state.metric];
    let html = "<div class='legend-title'>" + esc(m.legend) + "</div>";
    if (m.kind === "categorical") {
      html += "<div class='swatches'>" + m.legend_categories.map(c =>
        "<span class='swatch'><i style='background:" + m.colors[c] + "'></i>" + esc(c) + "</span>").join("") + "</div>";
    } else {
      const gradient = m.colorscale.map(([p, c]) => c + " " + (100 * p).toFixed(1) + "%").join(", ");
      html += "<div class='bar' style='background:linear-gradient(to right, " + gradient + ")'></div><div class='ticks'>" +
        m.ticks.map(t => "<span style='left:" + (100 * (t.value - m.zmin) / (m.zmax - m.zmin)).toFixed(2) + "%'>" +
          esc(t.label) + "</span>").join("") + "</div>";
      if (m.ends) html += "<div class='ends'><span>← " + esc(m.ends[0]) + "</span><span>" + esc(m.ends[1]) + " →</span></div>";
    }
    document.getElementById("legend").innerHTML = html;
  }

  function renderChips() {
    document.getElementById("chips").innerHTML = METRICS.map(m =>
      "<button class='chip' role='radio' data-metric='" + m.key + "' aria-checked='" + (m.key === state.metric) + "'>" +
      esc(m.label) + "</button>").join("");
    document.getElementById("metric-label").textContent = "Map · " + METRIC[state.metric].label;
  }

  // ---------- panel ----------
  const stat = (label, value, cls = "") =>
    "<div class='stat'><div class='label'>" + esc(label) + "</div><div class='value " + cls + "'>" + value + "</div></div>";

  function summaryHtml() {
    const r = S.regression;
    const direction = r.slope > 0 ? "more" : "less";
    return "<div class='panel-head'><div><div class='eyebrow'>Metropolitan France · " + S.n_departments +
      " départements</div><h2>Overview</h2></div></div>" +
      "<section class='block'><h3>Vote concentration (HHI)</h3><div class='stats'>" +
      stat("Mean " + FIRST, fixed(S.mean_hhi[FIRST])) + stat("Mean " + LAST, fixed(S.mean_hhi[LAST])) +
      stat("Higher in " + LAST, S.n_increase_hhi + "<span class='small'> of " + S.n_delta_hhi + "</span>") + "</div>" +
      "<p class='small'>Means are unweighted averages across départements (the typical département). " +
      (S.national_hhi ? "National HHI, from votes summed over the metropolitan départements: " +
        fixed(S.national_hhi[FIRST]) + " in " + FIRST + ", " + fixed(S.national_hhi[LAST]) + " in " + LAST + ". " : "") +
      "Higher = more concentrated.</p></section>" +
      "<section class='block'><h3>CENP (supplementary)</h3><div class='stats'>" +
      stat("Mean " + FIRST, fixed(S.mean_cenp[FIRST])) + stat("Mean " + LAST, fixed(S.mean_cenp[LAST])) +
      stat("Higher in " + LAST, S.n_increase + "<span class='small'> of " + S.n_delta + "</span>") + "</div>" +
      "<p class='small'>Normalised by the number of candidates (16 in " + FIRST + ", 12 in " + LAST +
      "), so it is not the main basis for comparing the two elections.</p></section>" +
      "<section class='block'><h3>Density relationship, " + r.year + "</h3><p class='small' style='margin:0'>" +
      "Across départements, the first-round vote was on average " + direction +
      " concentrated where population density was higher " +
      "(descriptive regression of CENP on log density, R² = " + r.r2.toFixed(2) + "). This is an association, " +
      "not a causal effect.</p></section>" +
      "<p class='prompt'>Select a département to explore its results.</p>";
  }

  function dumbbell(d, key = "hhi", measure = "HHI") {
    const a = d[key + "_" + FIRST], b = d[key + "_" + LAST];
    if (!isNum(a) || !isNum(b)) return "";
    const W = 320, pad = 14, y = 18, lo = S[key + "_min"], hi = S[key + "_max"], means = S["mean_" + key];
    const x = v => pad + (W - 2 * pad) * (v - lo) / (hi - lo);
    const meanTick = v => "<line x1='" + x(v) + "' x2='" + x(v) + "' y1='" + (y - 9) + "' y2='" + (y + 9) +
      "' stroke='#b5b5b0' stroke-width='1.5'/>";
    const left = a <= b;
    const label = (v, text, anchorLeft) => "<text x='" + (x(v) + (anchorLeft ? -8 : 8)) + "' y='" + (y + 22) +
      "' font-size='11' fill='#5f6368' text-anchor='" + (anchorLeft ? "end" : "start") + "'>" + text + "</text>";
    return "<svg class='dumbbell' viewBox='0 0 " + W + " 46' role='img' aria-label='" + measure + " " + fixed(a) + " in " + FIRST +
      " and " + fixed(b) + " in " + LAST + "'>" +
      "<line x1='" + pad + "' x2='" + (W - pad) + "' y1='" + y + "' y2='" + y + "' stroke='#ecece8' stroke-width='6' stroke-linecap='round'/>" +
      meanTick(means[FIRST]) + meanTick(means[LAST]) +
      "<line x1='" + x(a) + "' x2='" + x(b) + "' y1='" + y + "' y2='" + y + "' stroke='#1a1a1a' stroke-width='2'/>" +
      "<circle cx='" + x(a) + "' cy='" + y + "' r='5.5' fill='#fff' stroke='#1a1a1a' stroke-width='2'/>" +
      "<circle cx='" + x(b) + "' cy='" + y + "' r='5.5' fill='#1a1a1a'/>" +
      label(a, FIRST, left) + label(b, LAST, !left) + "</svg>" +
      "<div class='caption'>Scale: range of all départements in both years. Grey ticks: mean in " + FIRST + " and " + LAST + ".</div>";
  }

  function voteChart(d) {
    if (!DATA.candidates) {
      return "<p class='small' style='margin:0'>Candidate-level results were not available when this page was built.</p>";
    }
    const byYear = DATA.candidates[d.code] || {};
    const rows = byYear[state.voteYear] || [];
    const toggle = "<div class='toggle' role='group' aria-label='Election year'>" + DATA.years.map(y =>
      "<button data-vote-year='" + y + "' aria-pressed='" + (String(y) === state.voteYear) + "'>" + y + "</button>").join("") + "</div>";
    if (!rows.length) return toggle + "<p class='small' style='margin:0'>No candidate results for this département.</p>";
    const cliff = d["cliff_location_" + state.voteYear];
    const magnitude = d["cliff_magnitude_" + state.voteYear];
    const max = Math.max(...rows.map(r => r.share));
    let html = toggle + "<div class='votes'>";
    rows.forEach((r, i) => {
      html += "<div class='vrow" + (isNum(cliff) && i < cliff ? " above" : "") + "'><span class='vname' title='" + esc(r.name) + "'>" +
        esc(r.name) + "</span><span class='vtrack'><span class='vbar' style='width:" + (100 * r.share / max).toFixed(1) +
        "%'></span></span><span class='vpct'>" + pct(r.share) + "</span></div>";
      if (isNum(cliff) && i + 1 === cliff && i < rows.length - 1) {
        html += "<div class='vcliff'>largest drop" + (isNum(magnitude) ? ": " + (100 * magnitude).toFixed(1) + " pts" : "") + "</div>";
      }
    });
    return html + "</div><p class='small'>Share of valid first-round votes. Dark bars: candidates above the largest drop.</p>";
  }

  function departmentHtml(d) {
    const r = S.regression;
    const residual = d["residual_" + LAST];
    const verdict = !isNum(residual) ? "" : residual > 0 ? "More concentrated than predicted by density" : "Less concentrated than predicted by density";
    const rank = y => isNum(d["rank_" + y]) ? d["rank_" + y] + " of " + S.n_departments : "—";
    const cliffRow = y => "<tr><th>" + y + "</th><td>" + (isNum(d["cliff_location_" + y]) ? d["cliff_location_" + y] : "—") +
      "</td><td>" + (isNum(d["cliff_magnitude_" + y]) ? (100 * d["cliff_magnitude_" + y]).toFixed(1) + " pts" : "—") +
      "</td><td>" + fixed(d["cliff_ratio_" + y], 2) + "</td></tr>";
    return "<div class='panel-head'><div><div class='eyebrow'>Département " + esc(d.code) + "</div><h2>" + esc(d.name) +
      "</h2></div><button class='clear' data-action='clear'>Clear selection</button></div>" +

      "<section class='block'><h3>Vote concentration (HHI)</h3><div class='stats'>" +
      stat(FIRST, fixed(d["hhi_" + FIRST])) + stat(LAST, fixed(d["hhi_" + LAST])) +
      stat("Change", signed(d.delta_hhi)) + "</div>" + dumbbell(d, "hhi", "HHI") +
      "<p class='small'>Higher = more concentrated. Rank " + rank(FIRST) + " in " + FIRST + " · " + rank(LAST) + " in " +
      LAST + " (1 = most concentrated; the same ranking as with CENP within each election).</p></section>" +

      "<section class='block'><h3>CENP (supplementary)</h3><div class='stats'>" +
      stat(FIRST, fixed(d["cenp_" + FIRST])) + stat(LAST, fixed(d["cenp_" + LAST])) +
      stat("Change", signed(d.delta_cenp)) + "</div>" + dumbbell(d, "cenp", "CENP") +
      "<p class='small'>Normalised measure that depends on the number of candidates (16 in " + FIRST + ", 12 in " + LAST +
      "); the change in HHI is the main cross-election comparison.</p></section>" +

      "<section class='block'><h3>Population density</h3><div class='stats two'>" +
      stat(FIRST, densityFmt(d["density_" + FIRST]), "small") + stat(LAST, densityFmt(d["density_" + LAST]), "small") +
      "</div><p class='small'>Inhabitants per km².</p></section>" +

      "<section class='block'><h3>Relative to the " + LAST + " density relationship</h3><div class='stats'>" +
      stat("Predicted", fixed(d["fitted_" + LAST])) + stat("Actual", fixed(d["cenp_" + LAST])) +
      stat("Residual", signed(residual)) + "</div>" + (verdict ? "<div class='verdict'>" + verdict + "</div>" : "") +
      "<p class='small'>Prediction from a descriptive regression of CENP on log density across " + r.n +
      " départements (R² = " + r.r2.toFixed(2) + "). It describes an association, not a causal effect.</p></section>" +

      "<section class='block'><h3>Cliff metrics</h3><table class='mini'><thead><tr><th></th><th>Location</th>" +
      "<th>Magnitude</th><th>Ratio</th></tr></thead><tbody>" + DATA.years.map(cliffRow).join("") + "</tbody></table>" +
      "<p class='small'>Location: rank after which the largest drop between consecutive candidates occurs. " +
      "Magnitude: size of that drop. Ratio: how much it stands out from the other gaps (0.5–1).</p></section>" +

      "<section class='block'><h3>First-round vote shares</h3>" + voteChart(d) + "</section>";
  }

  function renderPanel() {
    const d = state.selected ? BY_CODE.get(state.selected) : null;
    document.getElementById("panel").innerHTML = d ? departmentHtml(d) : summaryHtml();
    document.getElementById("dep-select").value = d ? d.code : "";
  }

  // ---------- state ----------
  function writeHash() {
    const params = new URLSearchParams({ metric: state.metric });
    if (state.selected) params.set("dep", state.selected);
    try { history.replaceState(null, "", "#" + params.toString()); } catch (e) { /* e.g. sandboxed frames */ }
  }

  function readHash() {
    const params = new URLSearchParams(window.location.hash.slice(1));
    if (METRIC[params.get("metric")]) state.metric = params.get("metric");
    if (BY_CODE.has(params.get("dep"))) state.selected = params.get("dep");
  }

  function update({ map = true, panel = true } = {}) {
    if (map) { Plotly.react(mapEl, mapTraces(), LAYOUT, CONFIG); renderLegend(); renderChips(); }
    if (panel) renderPanel();
    writeHash();
  }

  function select(code) {
    state.selected = BY_CODE.has(code) ? code : null;
    update();
  }

  // ---------- events ----------
  document.getElementById("chips").addEventListener("click", e => {
    const button = e.target.closest("[data-metric]");
    if (button && button.dataset.metric !== state.metric) {
      state.metric = button.dataset.metric;  // the selection is kept
      update({ panel: false });
    }
  });
  document.getElementById("panel").addEventListener("click", e => {
    if (e.target.closest("[data-action='clear']")) return select(null);
    const yearButton = e.target.closest("[data-vote-year]");
    if (yearButton) { state.voteYear = yearButton.dataset.voteYear; update({ map: false }); }
  });
  const depSelect = document.getElementById("dep-select");
  depSelect.innerHTML = "<option value=''>Overview (all départements)</option>" +
    DEPS.map(d => "<option value='" + d.code + "'>" + esc(d.name) + " (" + d.code + ")</option>").join("");
  depSelect.addEventListener("change", () => select(depSelect.value || null));
  document.addEventListener("keydown", e => { if (e.key === "Escape" && state.selected) select(null); });

  readHash();
  Plotly.newPlot(mapEl, mapTraces(), LAYOUT, CONFIG).then(() => {
    mapEl.on("plotly_click", ev => {
      const point = ev && ev.points && ev.points[0];
      if (point && point.location) select(point.location);
    });
  });
  renderLegend();
  renderChips();
  renderPanel();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
