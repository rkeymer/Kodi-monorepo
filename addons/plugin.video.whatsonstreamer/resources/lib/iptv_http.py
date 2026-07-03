# -*- coding: utf-8 -*-
import time
import socket
import urllib.request
import urllib.error

try:
    import xbmc
    def _log(msg):
        xbmc.log(f"[WhatsOnStreamer][IPTV] {msg}", xbmc.LOGINFO)
except ImportError:
    def _log(msg):
        print(f"[IPTV] {msg}")

DEFAULT_BACKOFF_SECONDS = 2


def fetch_url(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    headers = {'User-Agent': 'WhatsOnStreamer/1.0 (Kodi)', 'Accept': '*/*', 'Accept-Encoding': 'gzip', 'Connection': 'close'}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                enc = resp.headers.get('Content-Encoding', '') if hasattr(resp, 'headers') else ''
                raw = resp.read()
                if enc and enc.lower() == 'gzip':
                    import gzip
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            last_err = e
            code = getattr(e, 'code', 0)
            if 500 <= code <= 599 and attempt < retries:
                _log(f"HTTP {code} on attempt {attempt}/{retries} - retrying")
                _backoff(attempt)
                continue
            raise
        except (socket.timeout, TimeoutError, urllib.error.URLError) as e:
            last_err = e
            if attempt < retries:
                _log(f"Network error on attempt {attempt}/{retries} - retrying")
                _backoff(attempt)
                continue
            raise
    _log(f"Failed to fetch URL after {retries} attempts: {last_err!r}")
    raise last_err


def _backoff(attempt: int):
    time.sleep(DEFAULT_BACKOFF_SECONDS * (2 ** (attempt - 1)))
