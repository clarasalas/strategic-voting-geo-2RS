"""Downloading pages of archives-resultats-elections.interieur.gouv.fr with an on-disk cache.

Each page is requested at most once: later runs read it from the cache. With `offline = True`
(or the environment variable SVGEO_OFFLINE=1) a page missing from the cache raises instead of being downloaded.
"""
import gzip
import os
import re
import time
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from svgeo.config import COMMUNE_CACHE_DIR, DEPARTMENT_CACHE_DIR

try:
    # Optional: requests with a real browser TLS fingerprint, used if the site answers 403
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

SITE_HOME = "https://www.archives-resultats-elections.interieur.gouv.fr/"
# The site answers 403 to non-browser clients: send the headers a normal browser sends
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": SITE_HOME,
}
TIMEOUT = 30
# A 403 in the middle of a long run is throttling: wait this long (seconds) and retry
FORBIDDEN_WAITS = [60, 300]


class Fetcher:
    """Pages from one cache folder.

    `compressed=True` (commune cache): gzip files named after host + path.
    `compressed=False` (département cache): plain files named after host + path + query.
    """

    def __init__(self, cache_dir, compressed, delay):
        self.cache_dir = cache_dir
        self.compressed = compressed
        self.delay = delay  # seconds between downloads (cached pages are not delayed)
        self.offline = os.environ.get("SVGEO_OFFLINE") == "1"
        self._session = None

    def cache_path(self, url):
        p = urlparse(url)
        if self.compressed:
            return self.cache_dir / (re.sub(r"[^A-Za-z0-9._-]+", "_", p.netloc + p.path) + ".gz")
        name = p.netloc + p.path + ("?" + p.query if p.query else "")
        return self.cache_dir / re.sub(r"[^A-Za-z0-9._-]+", "_", name)

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
            retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
            self._session.mount("https://", HTTPAdapter(max_retries=retry))
            try:
                self._session.get(SITE_HOME, timeout=TIMEOUT)  # pick up cookies set by the home page
            except requests.RequestException as e:
                print(f"Home page not reachable: {e!r}")
        return self._session

    def _download_once(self, url):
        resp = self.session.get(url, timeout=TIMEOUT)
        if resp.status_code == 403 and cffi_requests is not None:
            resp = cffi_requests.get(url, impersonate="chrome", timeout=TIMEOUT, headers={"Referer": SITE_HOME})
        return resp

    def download(self, url):
        resp = self._download_once(url)
        for wait in FORBIDDEN_WAITS:
            if resp.status_code != 403:
                break
            print(f"    403 on {url} — pausing {wait} s before retrying")
            time.sleep(wait)
            resp = self._download_once(url)
        if resp.status_code == 403:
            raise requests.HTTPError(f"403 Forbidden for {url}"
                                     + ("" if cffi_requests else " — try `pip install curl_cffi`"))
        resp.raise_for_status()
        return resp.content

    def fetch(self, url):
        """Raw bytes of a page, from the cache if available. Errors are never cached."""
        path = self.cache_path(url)
        if path.exists():
            data = path.read_bytes()
            return gzip.decompress(data) if self.compressed else data
        if self.offline:
            raise FileNotFoundError(f"not in the HTML cache (offline mode): {url}")
        content = self.download(url)
        time.sleep(self.delay)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(content) if self.compressed else content)
        return content

    def soup(self, url):
        # Bytes let BeautifulSoup use the page's <meta charset>: accents stay right for UTF-8 and ISO-8859-1
        return BeautifulSoup(self.fetch(url), "html.parser")


department_pages = Fetcher(DEPARTMENT_CACHE_DIR, compressed=False, delay=1.0)
commune_pages = Fetcher(COMMUNE_CACHE_DIR, compressed=True, delay=0.5)


def page_links(soup, base_url):
    """(absolute URL without fragment, label) for every <a href> of a page."""
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        out.append((urldefrag(urljoin(base_url, href))[0], " ".join(a.get_text(" ", strip=True).split())))
    return out
