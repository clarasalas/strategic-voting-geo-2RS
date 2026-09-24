"""Download the data from the Zenodo deposit and the département boundaries from Etalab, into data/.

Instead of scraping (scripts 01–02, several hours) and downloading the INSEE files by hand, this fetches the frozen
copy of every input and output used in the analysis:

  insee_inputs.zip   INSEE files                       -> data/raw/insee/
  raw_results.zip    scraped election results          -> data/raw/
  processed.zip      indices, density, audit tables    -> data/processed/
  html_pages.zip     scraped results pages (optional)  -> data/raw/html_cache*/   (--with-html-pages, ≈600 MB)
  département boundaries (Etalab, ODbL)                -> data/raw/geography/departements-100m.geojson

Every download is checked against its checksum. Existing files are kept unless --force is given.
Set SVGEO_DATA_DIR to extract into another data folder.

Usage: python scripts/download_data.py [--with-html-pages] [--force] [--from-dir DIR]
       --from-dir DIR   use deposit files already in DIR (e.g. data/zenodo) instead of downloading them
"""
import argparse
import hashlib
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import requests

from svgeo.config import (DATA_DIR, DEPARTMENT_BOUNDARIES, DEPARTMENT_BOUNDARIES_SHA256, DEPARTMENT_BOUNDARIES_URL,
                          ZENODO_RECORD)

ARCHIVES = ["insee_inputs.zip", "raw_results.zip", "processed.zip"]
HTML_ARCHIVE = "html_pages.zip"


def file_digest(path, algorithm):
    digest = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, target, algorithm, expected):
    """Stream `url` to `target` and check its checksum; the file is removed if the checksum differs."""
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(target, "wb") as f:
            for block in response.iter_content(1 << 20):
                f.write(block)
    actual = file_digest(target, algorithm)
    if actual != expected:
        target.unlink()
        raise ValueError(f"{url}: {algorithm} {actual} differs from the expected {expected}")


def zenodo_files():
    """{file name: (download url, md5)} of the published record."""
    if ZENODO_RECORD is None:
        raise SystemExit("ZENODO_RECORD is not set in svgeo/config.py yet: use --from-dir with local deposit files, "
                         "or rebuild the data with scripts 01–04 (see README).")
    record = requests.get(f"https://zenodo.org/api/records/{ZENODO_RECORD}", timeout=60)
    record.raise_for_status()
    return {f["key"]: (f["links"]["self"], f["checksum"].removeprefix("md5:")) for f in record.json()["files"]}


def extract(archive, force):
    """Extract an archive whose members are stored as data/...; members may not leave the data folder."""
    written = skipped = 0
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            parts = PurePosixPath(member.filename).parts
            if parts[0] != "data" or ".." in parts:
                raise ValueError(f"{archive.name}: unexpected member {member.filename}")
            target = DATA_DIR.joinpath(*parts[1:])
            if target.exists() and not force:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, open(target, "wb") as dst:
                while block := src.read(1 << 20):
                    dst.write(block)
            written += 1
    print(f"{archive.name}: {written} files written, {skipped} already present (kept)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-html-pages", action="store_true", help="also fetch the scraped pages (≈600 MB)")
    parser.add_argument("--force", action="store_true", help="overwrite files that already exist")
    parser.add_argument("--from-dir", type=Path, help="folder with the deposit files, instead of downloading")
    args = parser.parse_args()

    wanted = ARCHIVES + ([HTML_ARCHIVE] if args.with_html_pages else [])
    with tempfile.TemporaryDirectory() as tmp:
        if args.from_dir:
            paths = {name: args.from_dir / name for name in wanted}
        else:
            available = zenodo_files()
            paths = {}
            for name in wanted:
                url, md5 = available[name]
                print(f"Downloading {name} ...", flush=True)
                paths[name] = Path(tmp) / name
                download(url, paths[name], "md5", md5)
        for name in wanted:
            extract(paths[name], args.force)

    if DEPARTMENT_BOUNDARIES.exists() and not args.force:
        print(f"{DEPARTMENT_BOUNDARIES.name} exists — kept")
    else:
        DEPARTMENT_BOUNDARIES.parent.mkdir(parents=True, exist_ok=True)
        download(DEPARTMENT_BOUNDARIES_URL, DEPARTMENT_BOUNDARIES, "sha256", DEPARTMENT_BOUNDARIES_SHA256)
        print(f"{DEPARTMENT_BOUNDARIES.name}: downloaded from Etalab")


if __name__ == "__main__":
    main()
