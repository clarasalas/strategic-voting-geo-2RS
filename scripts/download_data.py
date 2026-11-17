"""Download the INSEE files and the département boundaries into data/, from their official sources.

  INSEE population estimates by département    -> data/raw/insee/1_Pop_annu_compo_evol_depreg.xlsx
  INSEE census series by commune (zip)          -> data/raw/insee/base-cc-serie-historique-2022.CSV
                                                   data/raw/insee/meta_base-cc-serie-historique-2022.CSV
  INSEE density grid 2025                       -> data/raw/insee/grille_densite_2025_geo2025.xlsx
  département boundaries (Etalab, ODbL)         -> data/raw/geography/departements-100m.geojson

Every file is checked against the SHA-256 recorded in svgeo/config.py, so a file that INSEE has since replaced with a
new version (a different commune geography, revised estimates) is refused instead of silently changing the results.
The election results come from scripts 01–02 (scraping) or scripts/results_from_datagouv.py (official files), which
check their own downloads.

Existing files are kept, but still checked, unless --force is given. Set SVGEO_DATA_DIR to use another data folder.

Usage: python scripts/download_data.py [--force]
       python scripts/download_data.py --checksums   # print the SHA-256 of the files already in data/
"""
import argparse
import hashlib
import io
import zipfile

import requests

from svgeo.config import (DEPARTMENT_BOUNDARIES, DEPARTMENT_BOUNDARIES_SHA256, DEPARTMENT_BOUNDARIES_URL,
                          INSEE_DOWNLOADS)

SOURCES = [*INSEE_DOWNLOADS,
           {"url": DEPARTMENT_BOUNDARIES_URL,
            "files": {DEPARTMENT_BOUNDARIES: DEPARTMENT_BOUNDARIES_SHA256}}]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check(path, expected, url):
    if expected is None:
        raise SystemExit(f"{path.name}: no SHA-256 recorded in svgeo/config.py yet (got {sha256(path)})")
    actual = sha256(path)
    if actual != expected:
        raise SystemExit(f"{path}: SHA-256 {actual} differs from the recorded {expected}.\n"
                         f"The source may have been replaced by a new version: {url}")


def fetch(source, force):
    """Download one source; a zip is opened in memory and only the listed members are written."""
    files = source["files"]
    if not force and all(path.exists() for path in files):
        for path, expected in files.items():
            check(path, expected, source["url"])
            print(f"{path.name}: present, checksum OK")
        return
    if source["url"] is None:
        raise SystemExit(f"{', '.join(p.name for p in files)}: no download address recorded in svgeo/config.py yet")
    print(f"Downloading {source['url']} ...", flush=True)
    response = requests.get(source["url"], timeout=120)
    response.raise_for_status()
    if source.get("zip"):
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            by_name = {name.rsplit("/", 1)[-1]: name for name in z.namelist()}
            missing = [path.name for path in files if path.name not in by_name]
            if missing:
                raise SystemExit(f"{source['url']}: {', '.join(missing)} not in the archive ({sorted(by_name)})")
            contents = {path: z.read(by_name[path.name]) for path in files}
    else:
        (path,) = files
        contents = {path: response.content}
    for path, data in contents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(data)
        try:
            check(partial, files[path], source["url"])
        except SystemExit:
            partial.unlink()
            raise
        partial.replace(path)
        print(f"{path.name}: downloaded, checksum OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="download again files that already exist")
    parser.add_argument("--checksums", action="store_true", help="print the SHA-256 of the local files and stop")
    args = parser.parse_args()

    if args.checksums:
        for source in SOURCES:
            for path in source["files"]:
                print(f"{sha256(path) if path.exists() else 'missing':64}  {path.name}")
        return
    for source in SOURCES:
        fetch(source, args.force)


if __name__ == "__main__":
    main()
