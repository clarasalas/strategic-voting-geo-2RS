"""Build the files of the Zenodo data deposit in data/zenodo/ (not versioned).

Each archive stores its files under their path from the repository root (`data/raw/...`, `data/processed/...`), so
unzipping it at the root of a clone, or running scripts/download_data.py, puts every file where the scripts expect it.

  insee_inputs.zip     data/raw/insee/: INSEE population estimates, census series and density grid
  raw_results.zip      data/raw/*.csv: scraped election results, département URLs, failed pages
  processed.zip        data/processed/: indices, density and audit tables written by the current pipeline
  html_pages.zip       data/raw/html_cache*/: every scraped results page (for scripts 01–02 with --offline)
  README.md            description of the deposit (also the text for the Zenodo record)
  SHA256SUMS           checksums of the files above

The département boundaries (Etalab, ODbL) are not included: download_data.py fetches them from Etalab.

Usage: python scripts/package_zenodo.py [--force]
"""
import argparse
import hashlib
import zipfile
from pathlib import Path

from svgeo.config import (AUDIT_DIR, COMMUNE_DENSITY, COMMUNE_DENSITY_BY_YEAR, COMMUNE_INDICES, COMMUNE_RESULTS,
                          DATA_DIR, DEPARTMENT_CACHE_DIR, COMMUNE_CACHE_DIR, DEPARTMENT_DENSITY,
                          DEPARTMENT_DENSITY_WIDE, DEPARTMENT_INDICES, DEPARTMENT_RESULTS, INSEE_DIR, RAW_DIR, ROOT)

OUT_DIR = DATA_DIR / "zenodo"
PROCESSED = [DEPARTMENT_RESULTS, COMMUNE_RESULTS, DEPARTMENT_INDICES, COMMUNE_INDICES, DEPARTMENT_DENSITY,
             DEPARTMENT_DENSITY_WIDE, COMMUNE_DENSITY, *COMMUNE_DENSITY_BY_YEAR.values()]


def archive_members():
    """{archive name: files}; every file must exist, so an incomplete data folder fails here, not silently."""
    members = {
        "insee_inputs.zip": sorted(p for p in INSEE_DIR.iterdir() if p.is_file() and not p.name.startswith(".")),
        "raw_results.zip": sorted(RAW_DIR.glob("*.csv")),
        "processed.zip": [*PROCESSED, *sorted(AUDIT_DIR.glob("*.csv"))],
        "html_pages.zip": sorted(p for d in (DEPARTMENT_CACHE_DIR, COMMUNE_CACHE_DIR) for p in d.rglob("*")
                                 if p.is_file() and not p.name.startswith(".")),
    }
    for name, files in members.items():
        missing = [p for p in files if not p.exists()]
        assert files and not missing, f"{name}: no files or missing {missing[:5]}"
    return members


def arcname(path):
    """Path inside the archive: from the repository root when the data folder is <repo>/data, else data/..."""
    return Path("data") / Path(path).resolve().relative_to(DATA_DIR.resolve())


def write_zip(target, files):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in files:
            # Cached commune pages are already gzip-compressed: store them as they are
            compress = zipfile.ZIP_STORED if path.suffix == ".gz" else zipfile.ZIP_DEFLATED
            z.write(path, arcname(path), compress_type=compress)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="rebuild archives that already exist")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    archives = archive_members()
    for name, files in archives.items():
        target = OUT_DIR / name
        if target.exists() and not args.force:
            print(f"{name} exists — skipped (use --force to rebuild)")
            continue
        print(f"{name}: {len(files)} files ...", flush=True)
        write_zip(target, files)
        print(f"  {target.stat().st_size / 1e6:.0f} MB")

    (OUT_DIR / "README.md").write_text((ROOT / "docs" / "zenodo_README.md").read_text(encoding="utf-8"),
                                       encoding="utf-8")
    # Only the deposit's own files: anything else left in the folder (e.g. an editor's history file) is ignored
    sums = [f"{sha256(OUT_DIR / name)}  {name}" for name in sorted(["README.md", *archives])]
    (OUT_DIR / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"Deposit files in {OUT_DIR}:")
    for line in sums:
        print(" ", line)


if __name__ == "__main__":
    main()
