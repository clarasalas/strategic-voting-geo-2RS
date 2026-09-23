"""Scraping first-round results by commune, starting from the département pages.

Structure of the site (observed on the cached pages):

* A département page (`…/presidentielle_2002/082/001/8201.php`, `…/presidentielle-2022/084/001/index.php`) links to
  alphabetical index pages in the same folder (`001a.php`, … in 2002; `001A.php`, … in 2022), which link to the
  commune pages.
* Commune pages are in the département folder and their file name contains the commune code:
  `…/026/058/58001.php` (2002: INSEE code) and `…/053/022/022179.php` (2022: 3-character département + commune number).
* Paris (département 75 = one commune): the département page shows the whole commune and links to the 20
  arrondissement pages. Lyon and Marseille have a whole-commune page linking to their arrondissements.
  Arrondissement pages (`…AR01.php`) are never treated as communes.
"""
import re
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from svgeo.config import DEPARTMENT_URLS, PLM
from svgeo.results_page import blank_null_total, extract_first_round, page_heading
from svgeo.web import commune_pages as pages, page_links

FAILURE_COLUMNS = ["year", "level", "department_code", "commune_code", "name", "url", "error"]
RAW_COLUMNS = ["year", "round", "department_code", "department_name", "commune_code", "commune_code_source",
               "commune_name", "registered", "abstentions", "voters", "blank_null", "expressed",
               "turnout_matches_sum", "page_heading", "source_url", "candidate", "votes", "vote_share"]


def url_stem(url):
    return Path(urlparse(url).path).stem


def url_folder(url):
    return Path(urlparse(url).path).parent.name


# Ministry letter codes of overseas territories -> first 2 digits of the INSEE commune code
# (the 3-digit commune number completes it: ZA + 101 -> 97101, ZN + 801 -> 98801)
OVERSEAS_LETTER_PREFIX = {"ZA": "97", "ZB": "97", "ZC": "97", "ZD": "97", "ZM": "97", "ZS": "97", "ZX": "97",
                          "ZN": "98", "ZP": "98", "ZW": "98"}
ARRONDISSEMENT_RE = re.compile(r"^0?(\d{2}\d{3})AR(\d{2})$", re.I)  # 75056AR01 / 075056AR01


def insee_code_from_stem(stem):
    """Commune INSEE code from a commune page file name; None if it is not a commune code.

    | stem     | meaning                                         | INSEE code |
    | 022179   | 3-character département + commune number (2022) | 22179      |
    | 02A004   | idem, Corsica                                   | 2A004      |
    | 58001    | INSEE code directly (2002)                      | 58001      |
    | 971101   | overseas département + 3-digit commune number   | 97101      |
    | ZA101    | Ministry letter code for overseas + number      | 97101      |
    """
    s = stem.upper()
    if re.fullmatch(r"0(\d{2}|2[AB])\d{3}", s):
        return s[1:]
    if re.fullmatch(r"9[78]\d\d{3}", s):
        return s[:2] + s[3:]
    if re.fullmatch(r"(\d{2}|2[AB])\d{3}", s):
        return s
    if re.fullmatch(r"Z[A-Z]\d{3}", s) and s[:2] in OVERSEAS_LETTER_PREFIX:
        return OVERSEAS_LETTER_PREFIX[s[:2]] + s[2:]
    return None


def department_from_commune(commune_code):
    """'01004' -> '01', '2A004' -> '2A', '97101' -> '971'."""
    return commune_code[:3] if commune_code.startswith(("97", "98")) else commune_code[:2]


# ---- Discovering commune URLs ----
def is_letter_page(url, folder):
    stem = url_stem(url)
    return (url_folder(url) == folder and len(stem) == len(folder) + 1
            and stem[:-1].upper() == folder.upper() and stem[-1].isalpha())


def discover_commune_urls(dep_url):
    """(communes, arrondissements, number of index pages, failed index pages) for one département page.

    1. reads the département page and keeps the alphabetical index pages;
    2. reads each index page and keeps the links, in the same folder, whose file name is a commune code;
    3. keeps arrondissement links apart;
    4. a page linking to arrondissements of a city without a commune link of its own (Paris) is used as the
       whole-commune page.
    """
    folder = url_folder(dep_url)
    dep_links = page_links(pages.soup(dep_url), dep_url)
    letter_pages = sorted({u for u, _ in dep_links if is_letter_page(u, folder)})
    link_pages, failed_pages = [(dep_url, dep_links)], []
    for u in letter_pages:
        try:
            link_pages.append((u, page_links(pages.soup(u), u)))
        except Exception as e:
            failed_pages.append((u, repr(e)))

    communes, arrondissements = {}, {}
    for page_url, links in link_pages:
        for url, label in links:
            if url_folder(url) != folder or url == dep_url:
                continue
            stem = url_stem(url)
            m = ARRONDISSEMENT_RE.fullmatch(stem)
            if m:
                arrondissements[url] = {"commune_code": m.group(1), "arrondissement": int(m.group(2)),
                                        "url": url, "label": label, "found_on": page_url}
                continue
            code = insee_code_from_stem(stem)
            if code and url not in communes:
                communes[url] = {"commune_code": code, "commune_code_source": stem, "commune_name_link": label,
                                 "url": url, "found_on": page_url}

    communes = pd.DataFrame(communes.values(),
                            columns=["commune_code", "commune_code_source", "commune_name_link", "url", "found_on"])
    arrondissements = pd.DataFrame(arrondissements.values(),
                                   columns=["commune_code", "arrondissement", "url", "label", "found_on"])
    for city_code in arrondissements["commune_code"].unique():
        if city_code not in set(communes["commune_code"]):
            page = arrondissements.loc[arrondissements["commune_code"] == city_code, "found_on"].iloc[0]
            communes.loc[len(communes)] = {"commune_code": city_code, "commune_code_source": url_stem(page),
                                           "commune_name_link": PLM.get(city_code, ""), "url": page, "found_on": page}
    return communes, arrondissements, len(letter_pages), failed_pages


def load_department_urls(year):
    """Département pages found by the département scraper; the unnamed duplicate '977' (= ZX) is dropped."""
    deps = pd.read_csv(DEPARTMENT_URLS[year], dtype={"dep_code": str}, encoding="utf-8")
    deps = deps[deps["dep_name"].notna()].reset_index(drop=True)
    return deps.rename(columns={"dep_code": "department_code", "dep_name": "department_name"})


def discover_all_communes(year, deps, limit=None, verbose=True):
    """Commune URLs of every département; failures recorded, loop continues."""
    communes, arrondissements, failures = [], [], []
    todo = deps if limit is None else deps.head(limit)
    for dep in todo.itertuples(index=False):
        fail = {"year": year, "department_code": dep.department_code, "commune_code": None,
                "name": dep.department_name}
        try:
            com, arr, n_letters, failed_pages = discover_commune_urls(dep.url)
        except Exception as e:
            failures.append({**fail, "level": "department", "url": dep.url, "error": repr(e)})
            print(f"  FAILED {dep.department_code} {dep.department_name}: {e!r}")
            continue
        for df in (com, arr):
            df.insert(0, "department_name", dep.department_name)
            df.insert(0, "department_code", dep.department_code)
            df.insert(0, "year", year)
        communes.append(com)
        arrondissements.append(arr)
        if verbose:
            print(f"  {dep.department_code} {dep.department_name}: {n_letters} index pages, {len(com)} communes"
                  + (f", {len(arr)} arrondissements" if len(arr) else "")
                  + (f" — {len(failed_pages)} index pages FAILED" if failed_pages else ""))
        for url, error in failed_pages:
            failures.append({**fail, "level": "index page", "url": url, "error": error})
        if com.empty and not failed_pages:  # territory whose results are not published by commune
            failures.append({**fail, "level": "no communes", "url": dep.url, "error": "no commune link found"})
    communes = pd.concat(communes, ignore_index=True) if communes else pd.DataFrame()
    arrondissements = pd.concat(arrondissements, ignore_index=True) if arrondissements else pd.DataFrame()
    print(f"{year}: {len(communes)} commune URLs, {len(arrondissements)} arrondissement URLs, {len(failures)} failures")
    return communes, arrondissements, failures


def retry_discovery(year, deps, commune_urls, arrondissement_urls, failures):
    """Re-discover the départements with a failed département or index page ('no communes' entries are kept)."""
    failures = pd.DataFrame(failures, columns=FAILURE_COLUMNS)
    to_redo = failures.loc[failures["level"].isin(["department", "index page"]), "department_code"].unique()
    if len(to_redo) == 0:
        return commune_urls, arrondissement_urls, failures.to_dict("records")
    print("Retrying discovery:", list(to_redo))
    new_com, new_arr, new_failures = discover_all_communes(year, deps[deps["department_code"].isin(to_redo)])
    keep = lambda df: df[~df["department_code"].isin(to_redo)] if len(df) else df  # noqa: E731
    commune_urls = pd.concat([keep(commune_urls), new_com], ignore_index=True)
    arrondissement_urls = pd.concat([keep(arrondissement_urls), new_arr], ignore_index=True)
    failures = [f for f in failures.to_dict("records") if f["department_code"] not in set(to_redo)] + new_failures
    return commune_urls, arrondissement_urls, failures


# ---- Scraping commune pages ----
def parse_commune_results(url, year, meta):
    """Long-format rows (one per candidate) for one commune page. `meta`: codes and names to attach."""
    soup = pages.soup(url)
    turnout, candidates, info = extract_first_round(soup, year)
    base = {
        "year": year, "round": 1, **meta,
        "registered": turnout.get("registered"), "abstentions": turnout.get("abstentions"),
        "voters": turnout.get("voters"), "blank_null": blank_null_total(turnout),
        "expressed": turnout.get("expressed"), "turnout_matches_sum": info["turnout_matches_sum"],
        "page_heading": page_heading(soup, loose=True), "source_url": url,
    }
    return pd.DataFrame([{**base, "candidate": c["candidate"], "votes": c["votes"], "vote_share": c["vote_share"]}
                         for c in candidates])


def scrape_communes(year, commune_urls, failures=None):
    """Parse every commune page; failures (URL, code, name, error) are recorded and the loop continues."""
    frames, failures = [], list(failures or [])
    n = len(commune_urls)
    for i, c in enumerate(commune_urls.itertuples(index=False), 1):
        meta = {"department_code": c.department_code, "department_name": c.department_name,
                "commune_code": c.commune_code, "commune_code_source": c.commune_code_source,
                "commune_name": c.commune_name_link}
        try:
            frames.append(parse_commune_results(c.url, year, meta))
        except Exception as e:
            failures.append({"year": year, "level": "commune", "department_code": c.department_code,
                             "commune_code": c.commune_code, "name": c.commune_name_link, "url": c.url,
                             "error": repr(e)})
        if i % 1000 == 0 or i == n:
            print(f"  {i}/{n} communes ({len(failures)} failures so far)")
    results = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RAW_COLUMNS)
    return results, pd.DataFrame(failures, columns=FAILURE_COLUMNS)


def retry_commune_failures(year, commune_urls, results, failures):
    """Re-scrape only the commune pages that failed; other failures are kept."""
    commune_failed = failures["level"].eq("commune")
    todo = commune_urls[commune_urls["url"].isin(failures.loc[commune_failed, "url"])]
    if todo.empty:
        return results, failures
    print(f"Retrying {len(todo)} commune pages")
    new_results, new_failures = scrape_communes(year, todo)
    results = pd.concat([results, new_results], ignore_index=True)
    failures = pd.concat([failures[~commune_failed], new_failures], ignore_index=True)
    return results, failures


# ---- Paris, Lyon, Marseille ----
def plm_check(results, year):
    """Scrape the arrondissement pages linked from the whole-commune pages and compare the candidate votes.

    Returns one row per city × candidate: whole-commune votes, sum over arrondissements, difference.
    """
    urls = []
    for page in results.loc[(results["year"] == year) & results["commune_code"].isin(PLM), "source_url"].unique():
        for url, _ in page_links(pages.soup(page), page):
            m = ARRONDISSEMENT_RE.fullmatch(url_stem(url))
            if m:
                urls.append({"commune_code": m.group(1), "arrondissement": int(m.group(2)), "url": url})
    urls = pd.DataFrame(urls, columns=["commune_code", "arrondissement", "url"]).drop_duplicates("url")
    print(f"{year}: arrondissement pages per city:", urls.groupby("commune_code").size().to_dict())
    frames = []
    for a in urls.itertuples(index=False):
        try:
            frames.append(parse_commune_results(a.url, year, {"commune_code": a.commune_code,
                                                              "arrondissement": a.arrondissement}))
        except Exception as e:
            print(f"  failed {a.url}: {e!r}")
    if not frames:
        return None
    arr = pd.concat(frames, ignore_index=True)
    arr_sum = arr.groupby(["commune_code", "candidate"])["votes"].sum().rename("sum_arrondissements")
    whole = (results[(results["year"] == year) & results["commune_code"].isin(PLM)]
             .set_index(["commune_code", "candidate"])["votes"].rename("whole_commune"))
    cmp = pd.concat([whole, arr_sum], axis=1)
    cmp["difference"] = cmp["whole_commune"] - cmp["sum_arrondissements"]
    return cmp
