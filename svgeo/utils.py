"""Text / number helpers and validation output."""
import re
import unicodedata

import pandas as pd

from svgeo.config import CODE_DTYPES


def strip_accents(text):
    return "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c))


def norm_text(text):
    """Lowercase, no accents, punctuation -> spaces, collapsed whitespace."""
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(text).lower()).strip()


def to_int(text):
    """'1 234 567' (any kind of space, incl. non-breaking) or '1.234.567' -> 1234567. None if not an integer."""
    s = re.sub(r"\s", "", str(text))
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return int(s) if re.fullmatch(r"\d+", s) else None


def to_float(text):
    """'24,01 %' -> 24.01. None if not a number."""
    s = re.sub(r"\s", "", str(text)).replace("%", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def read_csv(path, **kwargs):
    """UTF-8 CSV with every code column kept as text; floats read back exactly as written."""
    return pd.read_csv(path, dtype=CODE_DTYPES, encoding="utf-8", float_precision="round_trip", **kwargs)


def show(obj, n=None):
    """Display a table in a notebook, print it in a script."""
    if n is not None and hasattr(obj, "head"):
        obj = obj.head(n)
    try:
        get_ipython  # noqa: F821 — defined only inside IPython / Jupyter
        from IPython.display import display
        display(obj)
    except NameError:
        print(obj.to_string() if hasattr(obj, "to_string") else obj)


def report(label, ok, detail=None, n_show=20):
    """One validation line: [OK] or [CHECK]; the offending rows are shown when the check fails."""
    print(f"[{'OK' if ok else 'CHECK'}] {label}")
    if not ok and detail is not None and len(detail):
        show(detail, n_show)
