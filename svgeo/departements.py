"""Scraping first-round results by département.

1. Start from the national results page of each election.
2. Crawl its links (and, if needed, those of the region pages it links to) and recognise département pages from
   the link label ('Ain', 'Ain (01)') or, as a fallback, from a `region/département` URL pattern
   (`.../084/001/index.php`). No département URL is hard-coded.
3. Parse the first round on each département page (svgeo.results_page).
"""
import re
from urllib.parse import urldefrag, urljoin, urlparse

import pandas as pd

from svgeo.results_page import FIRST_ROUND_WORDS, ROUND_RE, blank_null_total, extract_first_round, page_heading
from svgeo.utils import norm_text, strip_accents
from svgeo.web import department_pages as pages

START_URLS = {
    2002: "https://www.archives-resultats-elections.interieur.gouv.fr/resultats/presidentielle_2002/index.php",
    2022: "https://www.archives-resultats-elections.interieur.gouv.fr/resultats/presidentielle-2022/index.php",
}

# ---- Reference list of départements ----
# Used only to recognise département links and to check for missing départements. Codes are INSEE codes;
# other overseas collectivités and French voters abroad keep their own codes and are flagged in `dep_type`.
METRO = """01;Ain|02;Aisne|03;Allier|04;Alpes-de-Haute-Provence|05;Hautes-Alpes|06;Alpes-Maritimes|07;Ardèche|
08;Ardennes|09;Ariège|10;Aube|11;Aude|12;Aveyron|13;Bouches-du-Rhône|14;Calvados|15;Cantal|16;Charente|
17;Charente-Maritime|18;Cher|19;Corrèze|2A;Corse-du-Sud|2B;Haute-Corse|21;Côte-d'Or|22;Côtes-d'Armor|23;Creuse|
24;Dordogne|25;Doubs|26;Drôme|27;Eure|28;Eure-et-Loir|29;Finistère|30;Gard|31;Haute-Garonne|32;Gers|33;Gironde|
34;Hérault|35;Ille-et-Vilaine|36;Indre|37;Indre-et-Loire|38;Isère|39;Jura|40;Landes|41;Loir-et-Cher|42;Loire|
43;Haute-Loire|44;Loire-Atlantique|45;Loiret|46;Lot|47;Lot-et-Garonne|48;Lozère|49;Maine-et-Loire|50;Manche|
51;Marne|52;Haute-Marne|53;Mayenne|54;Meurthe-et-Moselle|55;Meuse|56;Morbihan|57;Moselle|58;Nièvre|59;Nord|
60;Oise|61;Orne|62;Pas-de-Calais|63;Puy-de-Dôme|64;Pyrénées-Atlantiques|65;Hautes-Pyrénées|
66;Pyrénées-Orientales|67;Bas-Rhin|68;Haut-Rhin|69;Rhône|70;Haute-Saône|71;Saône-et-Loire|72;Sarthe|73;Savoie|
74;Haute-Savoie|75;Paris|76;Seine-Maritime|77;Seine-et-Marne|78;Yvelines|79;Deux-Sèvres|80;Somme|81;Tarn|
82;Tarn-et-Garonne|83;Var|84;Vaucluse|85;Vendée|86;Vienne|87;Haute-Vienne|88;Vosges|89;Yonne|
90;Territoire de Belfort|91;Essonne|92;Hauts-de-Seine|93;Seine-Saint-Denis|94;Val-de-Marne|95;Val-d'Oise"""

OUTRE_MER = [
    ("971", "Guadeloupe", "DOM"),
    ("972", "Martinique", "DOM"),
    ("973", "Guyane", "DOM"),
    ("974", "La Réunion", "DOM"),
    ("976", "Mayotte", "DOM"),  # DOM since 2011 (collectivité départementale in 2002)
    ("975", "Saint-Pierre-et-Miquelon", "COM"),
    ("986", "Wallis-et-Futuna", "COM"),
    ("987", "Polynésie française", "COM"),
    ("988", "Nouvelle-Calédonie", "COM"),
    ("ZX", "Saint-Martin/Saint-Barthélemy", "COM"),
    ("ZZ", "Français établis hors de France", "etranger"),
]

DEPARTEMENTS = pd.DataFrame(
    [(*item.strip().split(";"), "metropole") for item in METRO.split("|")] + OUTRE_MER,
    columns=["dep_code", "dep_name", "dep_type"],
)

# Départements in the strict sense for each election (used by the "missing départements" check)
_strict = DEPARTEMENTS.loc[DEPARTEMENTS["dep_type"].isin(["metropole", "DOM"]), "dep_code"]
EXPECTED_DEPS = {2002: set(_strict) - {"976"},  # Mayotte was not yet a département in 2002
                 2022: set(_strict)}

# Letter codes used by the Ministère de l'Intérieur for overseas territories (in URLs)
MININT_CODES = {"ZA": "971", "ZB": "972", "ZC": "973", "ZD": "974", "ZM": "976", "ZS": "975",
                "ZW": "986", "ZP": "987", "ZN": "988", "ZX": "ZX", "ZZ": "ZZ", "099": "ZZ"}

# Alternative spellings that may be used in link labels
NAME_ALIASES = {
    "Réunion": "974", "Guyane française": "973", "Côtes-du-Nord": "22",
    "Français de l'étranger": "ZZ", "Français établis hors de France": "ZZ", "Étranger": "ZZ",
    "Saint-Martin et Saint-Barthélemy": "ZX", "Saint-Barthélemy et Saint-Martin": "ZX",
    "Saint-Barthélemy/Saint-Martin": "ZX",
}


def norm_dep_name(text):
    """'Ain (01)', '01 - Ain', 'Département de l'Ain' -> 'ain'."""
    key = norm_text(re.sub(r"\(.*?\)", " ", str(text)))
    key = re.sub(r"^departement( de la| de l| du| des| de| d)? ", "", key)
    key = re.sub(r"^(0?\d{2,3}|0?2[ab]|z[a-z]) ", "", key)  # leading code
    key = re.sub(r" (0?\d{2,3}|0?2[ab]|z[a-z])$", "", key)  # trailing code
    return key


NAME_TO_CODE = {norm_dep_name(name): code for code, name in zip(DEPARTEMENTS["dep_code"], DEPARTEMENTS["dep_name"])}
NAME_TO_CODE.update({norm_dep_name(name): code for name, code in NAME_ALIASES.items()})

# ---- Link discovery ----
# Département links may be <a>, <area> of an image map, <a xlink:href> in an SVG map, data-* attributes,
# onclick handlers or <option>s of a drop-down list.
LINK_ATTRS = ["href", "xlink:href", "data-href", "data-url", "data-link"]
CODE_SEG_RE = re.compile(r"\d{2,3}|0?2[AB]|Z[A-Z]", re.I)


def iter_links(soup, base_url):
    """Yield (absolute_url, label) for every link-like element of the page."""
    for tag in soup.find_all(True):
        target = next((tag.get(a) for a in LINK_ATTRS if tag.get(a)), None)
        if target is None and tag.get("onclick"):
            m = re.search(r"['\"]([^'\"]+\.(?:php|html?)[^'\"]*)['\"]", tag["onclick"])
            target = m.group(1) if m else None
        if target is None and tag.name == "option" and re.search(r"\.(php|html?)|/", tag.get("value", "")):
            target = tag["value"]
        if not target or target.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urldefrag(urljoin(base_url, target.strip()))[0]
        label = (tag.get_text(" ", strip=True) or tag.get("title") or tag.get("alt")
                 or tag.get("aria-label") or tag.get("data-name") or "")
        yield url, label


def code_segments(url, root):
    """For .../<root>/084/001/index.php return ['084', '001']; None if the URL is not of that form."""
    parts = [p for p in url[len(root):].split("?")[0].split("/") if p]
    if not parts or not re.fullmatch(r"index\.(php|html?)", parts[-1]):
        return None
    parts = parts[:-1]
    return parts if parts and all(CODE_SEG_RE.fullmatch(p) for p in parts) else None


def code_from_url(url):
    """Département code from the last code-like URL segment.

    .../084/001/index.php -> '01', .../02A/index.php -> '2A', .../ZA/index.php -> '971'
    """
    parts = [p for p in urlparse(url).path.split("/") if p]
    if parts and re.match(r"index\.", parts[-1]):
        parts = parts[:-1]
    elif parts:
        parts[-1] = parts[-1].rsplit(".", 1)[0]
    for seg in reversed(parts):
        seg = seg.upper()
        if seg in MININT_CODES:
            return MININT_CODES[seg]
        m = re.fullmatch(r"0?(\d[\dAB])", seg)
        if m:
            return m.group(1)
        if re.fullmatch(r"9[78]\d", seg):
            return seg
    return None


def show_links(url, pattern=None):
    """Debugging: every link found on a page (optionally only those whose URL/label matches `pattern`)."""
    rows = pd.DataFrame(list(iter_links(pages.soup(url), url)), columns=["url", "label"]).drop_duplicates()
    if pattern:
        rows = rows[rows["url"].str.contains(pattern, case=False) | rows["label"].str.contains(pattern, case=False)]
    return rows


def discover_departement_urls(year, max_depth=2, max_pages=300):
    """Crawl from the national page and return one URL per département.

    - A link whose label is a département name is a département link.
    - Fallback: a link of the form <root>/<region>/<dep>/index.php is a département link.
    - Other index pages below the election root (region pages) are explored, down to `max_depth` clicks.
    Département pages themselves are never explored (their links point to communes).
    """
    start_url = START_URLS[year]
    root = start_url.rsplit("/", 1)[0] + "/"
    found, visited, queue = [], set(), [(start_url, 0)]
    while queue and len(visited) < max_pages:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            soup = pages.soup(url)
        except Exception as e:
            print(f"  could not fetch {url}: {e!r}")
            continue
        for link, label in iter_links(soup, url):
            if not link.startswith(root) or link in visited:
                continue
            segs = code_segments(link, root)
            code, method = NAME_TO_CODE.get(norm_dep_name(label)), "link label"
            if code is None and segs and len(segs) == 2:
                code, method = code_from_url(link), "url pattern"
            if code:
                found.append({"dep_code": code, "url": link, "link_label": label, "method": method,
                              "found_on": url, "path_depth": len(urlparse(link).path.strip("/").split("/"))})
            elif segs and depth < max_depth:
                queue.append((link, depth + 1))

    columns = ["year", "dep_code", "dep_name", "dep_type", "url", "link_label", "method", "found_on"]
    print(f"{year}: visited {len(visited)} pages, {len(found)} département links found")
    if not found:
        print(f"No département link recognised. Inspect the start page with show_links({start_url!r}).")
        return pd.DataFrame(columns=columns)

    links = pd.DataFrame(found)
    # Several links can point to the same département (map + list, region page + département page, ...).
    # Prefer: not a 2nd-round URL, matched by label, deepest URL (département page rather than region page).
    links["round2_hint"] = links["url"].str.contains(r"tour\W?2|/t2/|2nd|second", case=False, regex=True)
    links["by_label"] = links["method"].eq("link label")
    links = (links.sort_values(["dep_code", "round2_hint", "by_label", "path_depth"],
                               ascending=[True, True, False, False], kind="stable")
                  .drop_duplicates("dep_code"))
    links = links.merge(DEPARTEMENTS, on="dep_code", how="left")
    links.insert(0, "year", year)
    links = links[columns]

    missing = sorted(EXPECTED_DEPS[year] - set(links["dep_code"]))
    print(f"{year}: {len(links)} distinct départements/territories; missing (strict sense): {missing or 'none'}")
    return links.reset_index(drop=True)


# ---- Parsing département pages ----
# Output columns keep the French names used in the saved files
TURNOUT_COLUMNS = {"registered": "inscrits", "abstentions": "abstentions", "voters": "votants",
                   "blank_null": "blancs_nuls", "expressed": "exprimes"}


def parse_results_page(url, year):
    """Round 1 of a page; follows a '1er tour' link if round 1 is not on the page itself."""
    soup = pages.soup(url)
    try:
        return extract_first_round(soup, year) + (soup, url)
    except ValueError:
        for link, label in iter_links(soup, url):
            m = ROUND_RE.search(norm_text(label))
            if m and m.group(1) in FIRST_ROUND_WORDS and link != url:
                soup = pages.soup(link)
                return extract_first_round(soup, year) + (soup, link)
        raise


def parse_departement(url, year, dep_code, dep_name):
    """Long format: one row per candidate, turnout figures repeated on each row."""
    turnout, candidates, info, soup, page_url = parse_results_page(url, year)
    base = {"year": year, "round": 1, "dep_code": dep_code, "dep_name": dep_name}
    base.update({fr: turnout.get(en) for en, fr in TURNOUT_COLUMNS.items()})
    base["blancs_nuls"] = blank_null_total(turnout)
    df = pd.DataFrame([{**base, **c} for c in candidates])
    df["source_url"] = page_url
    df["table_round_label"] = info["table_round_label"]
    df["turnout_matches_sum"] = info["turnout_matches_sum"]
    df["page_heading"] = page_heading(soup)
    return df


def scrape_year(year, dep_urls, limit=None):
    """Parse every département page; failures are recorded (not dropped) and the loop continues."""
    todo = dep_urls if limit is None else dep_urls.head(limit)
    frames, failures = [], []
    for i, row in enumerate(todo.itertuples(index=False), 1):
        try:
            frames.append(parse_departement(row.url, year, row.dep_code, row.dep_name))
        except Exception as e:
            failures.append({"year": year, "dep_code": row.dep_code, "dep_name": row.dep_name,
                             "url": row.url, "error": repr(e)})
            print(f"  FAILED {row.dep_code} {row.dep_name}: {e!r}")
    results = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    failures = pd.DataFrame(failures, columns=["year", "dep_code", "dep_name", "url", "error"])
    print(f"{year}: {len(frames)} départements parsed, {len(failures)} failures")
    return results, failures


def retry_failures(year, dep_urls, results, failures):
    """Re-run the failed départements only and merge them into the results."""
    todo = dep_urls[dep_urls["dep_code"].isin(failures["dep_code"])]
    new_results, new_failures = scrape_year(year, todo)
    return pd.concat([results, new_results], ignore_index=True), new_failures


# ---- Combining both years ----
CIVILITY_RE = r"^(M\.|Mme\.?|Mlle\.?|Monsieur|Madame)\s+"
RESULT_COLUMNS = [
    "year", "round", "dep_code", "dep_name", "dep_type",
    "inscrits", "abstentions", "votants", "blancs_nuls", "exprimes",
    "candidate", "candidate_clean", "candidate_surname", "votes", "vote_share", "vote_share_calc",
    "source_url", "page_heading", "table_round_label", "turnout_matches_sum",
]


def surname(name):
    """Upper-case words of the name: 'Mme Marine LE PEN' -> 'LE PEN'."""
    words = re.findall(r"[^\s]+", re.sub(CIVILITY_RE, "", name))
    upper = [w for w in words if len(w) > 1 and w.upper() == w and re.search(r"[A-Z]", strip_accents(w))]
    return " ".join(upper) if upper else name


def combine(frames):
    """Both years in one table, with dep_type, candidate name without civility, surname, recomputed share."""
    results = pd.concat(frames, ignore_index=True)
    results = results.merge(DEPARTEMENTS[["dep_code", "dep_type"]], on="dep_code", how="left")
    results["candidate_clean"] = results["candidate"].str.replace(CIVILITY_RE, "", regex=True).str.strip()
    # 2002 names are written "M.  JEAN-MARIE  LE PEN": the surname was split at parse time;
    # otherwise fall back to the upper-case words of the name ("Mme Marine LE PEN" -> "LE PEN")
    results["candidate_surname"] = results["candidate_surname"].fillna(results["candidate"].map(surname))
    results["vote_share_calc"] = 100 * results["votes"] / results["exprimes"]
    return (results[RESULT_COLUMNS]
            .sort_values(["year", "dep_code", "votes"], ascending=[True, True, False]).reset_index(drop=True))
