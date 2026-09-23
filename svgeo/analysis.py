"""Descriptive statistics and plots used by the analysis notebooks. Everything is descriptive, nothing causal."""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats

from svgeo.config import YEARS


def association_stats(df, y, weighted=False, x_log="log_density", x_raw="density"):
    """Pearson (y vs log density), Spearman (y vs density — same as with log density), OLS y ~ log_density
    (coefficient, SE, HC1-robust SE, p-values, R²); optionally WLS weighted by expressed votes (HC1)."""
    df = df[[y, x_log, x_raw] + (["expressed"] if "expressed" in df else [])].dropna()
    pearson = stats.pearsonr(df[y], df[x_log])
    spearman = stats.spearmanr(df[y], df[x_raw])
    ols = smf.ols(f"{y} ~ {x_log}", data=df).fit()
    hc1 = ols.get_robustcov_results(cov_type="HC1")
    out = {
        "n": int(ols.nobs),
        "pearson_r": pearson.statistic, "pearson_p": pearson.pvalue,
        "spearman_rho": spearman.statistic, "spearman_p": spearman.pvalue,
        "ols_coef": ols.params[x_log], "ols_se": ols.bse[x_log], "ols_se_HC1": hc1.bse[1],
        "ols_p": ols.pvalues[x_log], "ols_p_HC1": hc1.pvalues[1], "ols_R2": ols.rsquared,
    }
    if weighted:
        wls = smf.wls(f"{y} ~ {x_log}", data=df, weights=df["expressed"]).fit(cov_type="HC1")
        out.update({"wls_coef": wls.params[x_log], "wls_se_HC1": wls.bse[x_log], "wls_p_HC1": wls.pvalues[x_log],
                    "wls_R2": wls.rsquared})
    return out


def stats_by_threshold(samples, y="CENP", thresholds=(0, 100, 500, 1000), weighted=False):
    """association_stats for every sample × year × minimum number of expressed votes."""
    rows = []
    for name, df in samples.items():
        for year in YEARS:
            for t in thresholds:
                sub = df[(df["year"] == year) & (df["expressed"] >= t)]
                rows.append({"sample": name, "year": year, "min_expressed": t,
                             **association_stats(sub, y, weighted=weighted)})
    return pd.DataFrame(rows).set_index(["sample", "year", "min_expressed"])


def fe_stats(d, y="CENP"):
    """y ~ log_density + département fixed effects (HC1 and département-clustered SE), and the slope without FE."""
    d = d[[y, "log_density", "department_code"]].dropna()
    formula = f"{y} ~ log_density + C(department_code)"
    hc1 = smf.ols(formula, data=d).fit(cov_type="HC1")
    cl = smf.ols(formula, data=d).fit(cov_type="cluster", cov_kwds={"groups": d["department_code"]})
    no_fe = smf.ols(f"{y} ~ log_density", data=d).fit(cov_type="HC1")
    return {"n": int(hc1.nobs), "n_departements": d["department_code"].nunique(),
            "coef": hc1.params["log_density"], "se_HC1": hc1.bse["log_density"], "p_HC1": hc1.pvalues["log_density"],
            "se_cluster_dep": cl.bse["log_density"], "p_cluster_dep": cl.pvalues["log_density"], "R2": hc1.rsquared,
            "coef_without_FE": no_fe.params["log_density"]}


# ---- Plots ----
def department_scatter(df, x, y, ax, title, xlabel, ylabel, trend=True, n_label=2, always_label=("75",)):
    """One point per département, optional OLS line; labels for Paris and the n_label highest / lowest values."""
    ax.scatter(df[x], df[y], s=18, alpha=0.7)
    if trend:
        slope, intercept = np.polyfit(df[x], df[y], 1)
        xs = np.linspace(df[x].min(), df[x].max(), 100)
        ax.plot(xs, intercept + slope * xs, color="C1", lw=1.5, label=f"linear fit (slope {slope:.3f})")
        ax.legend(loc="best", fontsize=8)
    to_label = pd.concat([df[df["department_code"].isin(always_label)], df.nlargest(n_label, y), df.nsmallest(n_label, y)])
    for _, row in to_label.drop_duplicates("department_code").iterrows():
        ax.annotate(row["department_name"], (row[x], row[y]), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_title(title, fontsize=10)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)


def commune_scatter(df, y, ax, title, ylabel, x="log_density", xlabel="log(population density, inhabitants/km²)"):
    """Tens of thousands of points: small transparent dots, mean of y within 20 bins of x, and the OLS line."""
    ax.scatter(df[x], df[y], s=2, alpha=0.08, color="C0", rasterized=True)
    binned = df.groupby(pd.cut(df[x], 20), observed=True).agg(x=(x, "mean"), y=(y, "mean"))
    ax.plot(binned["x"], binned["y"], "o-", color="black", ms=3, lw=1, label="mean within bins")
    slope, intercept = np.polyfit(df[x], df[y], 1)
    xs = np.linspace(df[x].min(), df[x].max(), 100)
    ax.plot(xs, intercept + slope * xs, color="C1", lw=1.5, label=f"OLS line (slope {slope:.3f})")
    ax.set_title(title, fontsize=10)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.legend(fontsize=8)


def pooled_interaction(d, cov="cluster", y="CENP"):
    """y ~ log_density × C(year) on stacked cross-sections; département-clustered or HC1 SE."""
    d = d.dropna(subset=[y, "log_density"])
    kwargs = ({"cov_type": "cluster", "cov_kwds": {"groups": d["department_code"]}} if cov == "cluster"
              else {"cov_type": "HC1"})
    return smf.ols(f"{y} ~ log_density * C(year)", data=d).fit(**kwargs)


def show_plot(fig):
    fig.tight_layout()
    plt.show()
