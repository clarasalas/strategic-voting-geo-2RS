"""Parsing the first-round results shown on a results page (département or commune: same template).

Observed structure: small HTML tables — turnout (`Inscrits / Abstentions / Votants`, `Blancs ou nuls` or
`Blancs` + `Nuls`, `Exprimés`) and candidates (`Voix`, `% Exprimés`). Pages show round 2 first, then
"Rappel … 1er tour". The parser:

1. reads every (non-layout) <table> into rows of cell texts;
2. recognises candidate tables (a header cell `Voix`) and turnout tables (rows `Inscrits`, `Exprimés`, …);
3. keeps the candidate table with more than 2 candidates — round 2 always has exactly 2;
4. merges the turnout tables printed just above it (2002 layout) and, separately, just below it (2022 layout),
   and keeps the block whose `Exprimés` equals the sum of the candidates' votes.
"""
import re

from svgeo.config import EXPECTED_K
from svgeo.utils import norm_text, to_float, to_int

TURNOUT_FIELDS = [  # order matters: 'blancs ou nuls' before 'blancs'
    ("blancs ou nuls", "blank_null"), ("blancs et nuls", "blank_null"), ("blancs", "blank"), ("nuls", "null"),
    ("inscrits", "registered"), ("abstention", "abstentions"), ("votants", "voters"), ("exprimes", "expressed"),
]
ROUND_RE = re.compile(r"\b(1er|1 er|premier|2nd|2 nd|2d|2e|2eme|second|deuxieme)\s+tour\b")
FIRST_ROUND_WORDS = ("1er", "1 er", "premier")
VOTES_HEADER_RE = r"^(voix|nombre de voix)$"


def table_rows(table):
    """List of rows, each a list of cell texts (inner spacing kept, e.g. 'M.  JACQUES  CHIRAC')."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    return rows


def turnout_label(cell):
    key = re.sub(r"^(nombre (de |d )?|bulletins |votes )", "", norm_text(cell))
    return next((field for prefix, field in TURNOUT_FIELDS if key.startswith(prefix)), None)


def header_index(rows, pattern):
    return next((i for i, row in enumerate(rows) if any(re.search(pattern, norm_text(c)) for c in row)), None)


def split_surname(raw_name):
    """2002 pages separate civility, first name and surname with double spaces:
    'M.  JEAN-MARIE  LE PEN' -> 'LE PEN'. None if the name is not written that way."""
    parts = re.split(r"\s{2,}", raw_name.strip())
    return parts[-1] if len(parts) >= 3 else None


def parse_candidates(rows):
    """[{'candidate', 'candidate_surname', 'votes', 'vote_share'}] from a table with a 'Voix' column; [] otherwise."""
    h = header_index(rows, VOTES_HEADER_RE)
    if h is None:
        return []
    header = [norm_text(c) for c in rows[h]]
    votes_col = next(i for i, c in enumerate(header) if re.search(VOTES_HEADER_RE, c))
    share_col = next((i for i, c in enumerate(header) if "exprim" in c), None)
    name_col = next((i for i, c in enumerate(header) if re.search(r"^(candidats?|listes?|noms?)\b", c)), 0)
    out = []
    for row in rows[h + 1:]:
        if len(row) != len(header) or turnout_label(row[0]) or turnout_label(row[name_col]):
            continue
        votes = to_int(row[votes_col])
        if votes is None or not re.search(r"[A-Za-z]", row[name_col]):
            continue
        out.append({
            "candidate": " ".join(row[name_col].split()),
            "candidate_surname": split_surname(row[name_col]),
            "votes": votes,
            "vote_share": to_float(row[share_col]) if share_col is not None else None,
        })
    return out


def parse_turnout(rows):
    """Turnout figures found in a table, e.g. {'registered': ..., 'voters': ...}; None if there are none.

    May be partial: on the 2002 pages Inscrits/Abstentions/Votants and Blancs ou nuls/Exprimés are two tables.
    """
    h = header_index(rows, r"^nombre$")
    col = [norm_text(c) for c in rows[h]].index("nombre") if h is not None else 1
    values = {}
    for row in rows:
        field = turnout_label(row[0])
        if field and field not in values and len(row) > col and to_int(row[col]) is not None:
            values[field] = to_int(row[col])
    if not values:  # transposed layout: labels in one row, numbers in the next one
        for i, row in enumerate(rows[:-1]):
            fields = [turnout_label(c) for c in row]
            if sum(f is not None for f in fields) >= 3:
                values = {f: to_int(v) for f, v in zip(fields, rows[i + 1]) if f and to_int(v) is not None}
                break
    return values or None


def guess_round(table):
    """1 or 2 if the table's caption, container id/class or nearest preceding text says so, else None."""
    texts = [table.caption.get_text(" ")] if table.caption else []
    for parent in [table, *table.parents]:
        attrs = " ".join([parent.get("id") or "", *(parent.get("class") or [])]) if hasattr(parent, "get") else ""
        m = re.search(r"tour\s?([12])\b|\bt([12])\b", norm_text(attrs))
        if m:
            return int(m.group(1) or m.group(2))
    texts += list(table.find_all_previous(string=True, limit=40))
    for text in texts:
        m = ROUND_RE.search(norm_text(text))
        if m:
            return 1 if m.group(1) in FIRST_ROUND_WORDS else 2
    return None


def blank_null_total(turnout):
    """Blank + null ballots: one line in 2002 ('Blancs ou nuls'), two lines in 2022 ('Blancs', 'Nuls')."""
    if turnout.get("blank_null") is not None:
        return turnout["blank_null"]
    if "blank" in turnout and "null" in turnout:
        return turnout["blank"] + turnout["null"]
    return None


def extract_first_round(soup, year):
    """(turnout dict, candidate list, info dict) for the first round shown on a page. ValueError if not found."""
    cand_tables, turnout_tables = [], []
    for pos, table in enumerate(soup.find_all("table")):
        if table.find("table"):  # layout table wrapping other tables
            continue
        rows = table_rows(table)
        cands, turnout = parse_candidates(rows), parse_turnout(rows)
        if cands:
            cand_tables.append({"pos": pos, "table": table, "cands": cands})
        if turnout:
            turnout_tables.append({"pos": pos, "values": turnout})

    first = [t for t in cand_tables if len(t["cands"]) > 2]  # round 2 has exactly 2 candidates
    if not first:
        raise ValueError(f"no first-round candidate table (candidate table sizes: {[len(t['cands']) for t in cand_tables]})")
    for t in first:
        t["round"] = guess_round(t["table"])
    first.sort(key=lambda t: (t["round"] == 2, len(t["cands"]) != EXPECTED_K[year], t["pos"]))
    cand = first[0]
    total = sum(c["votes"] for c in cand["cands"])

    # Turnout tables of this candidate table are printed just above it (between the previous candidate table and
    # this one) or just below it (between this one and the next). One of the two blocks belongs to the other round:
    # keep the block whose 'Exprimés' equals the sum of the candidates' votes.
    positions = [t["pos"] for t in cand_tables]
    prev_pos = max((p for p in positions if p < cand["pos"]), default=-1)
    next_pos = min((p for p in positions if p > cand["pos"]), default=float("inf"))
    merged = []
    for block in ([t for t in turnout_tables if prev_pos < t["pos"] <= cand["pos"]],
                  [t for t in turnout_tables if cand["pos"] <= t["pos"] < next_pos]):
        values = {}
        for t in block:
            for k, v in t["values"].items():
                values.setdefault(k, v)
        if "expressed" in values and "registered" in values:
            merged.append(values)
    if not merged:
        raise ValueError("no turnout (Inscrits ... Exprimés) found next to the first-round candidates")
    # Otherwise the first block, flagged by turnout_matches_sum = False (caught by the validation checks)
    turnout = next((t for t in merged if t["expressed"] == total), merged[0])
    info = {"table_round_label": cand["round"], "turnout_matches_sum": turnout["expressed"] == total}
    return turnout, cand["cands"], info


def page_heading(soup, loose=False):
    """Results title such as 'PARIS (75) (résultats officiels)' (the <h1>/<title> are generic).

    Looks for a heading or <strong> with a code in parentheses; `loose` (commune pages) also accepts
    any code-like parenthesis or the word 'résultats'.
    """
    pattern = r"\(\s*[0-9A-Z]{2,3}\s*\)" if loose else r"\(\s*(\d{2,3}|2[AB]|Z[A-Z])\s*\)"
    for tag in soup.find_all(["strong", "h1", "h2", "h3"]):
        text = " ".join(tag.get_text(" ", strip=True).split())
        if re.search(pattern, text) or (loose and "sultats" in text):
            return text
    tag = soup.find("title")
    return tag.get_text(" ", strip=True) if tag else ""


def describe_tables(soup, n_rows=5):
    """Debugging: print a summary of every table on a page."""
    print("Heading:", page_heading(soup))
    for pos, table in enumerate(soup.find_all("table")):
        rows = table_rows(table)
        print(f"\n--- table {pos}: {len(rows)} rows | round label: {guess_round(table)} | "
              f"layout table: {bool(table.find('table'))}")
        for row in rows[:n_rows]:
            print("   ", row)
