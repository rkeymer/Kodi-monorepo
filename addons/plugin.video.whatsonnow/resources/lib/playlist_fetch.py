# -*- coding: utf-8 -*-
import io
import time
import socket
import urllib.request
import urllib.error

from .m3u import iter_m3u
from . import log

DEFAULT_BACKOFF_SECONDS = 2

def fetch_url(url: str, timeout: int = 180, retries: int = 3) -> bytes:
    headers = {'User-Agent': 'WhatsOnNow/1.0 (Kodi)', 'Accept': '*/*', 'Accept-Encoding': 'gzip', 'Connection': 'close'}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            log.debug(f"HTTP GET attempt {attempt}/{retries} timeout={timeout}s")
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
                log.warn(f"HTTP {code} on attempt {attempt}/{retries} - retrying")
                _backoff(attempt)
                continue
            raise
        except (socket.timeout, TimeoutError, urllib.error.URLError) as e:
            last_err = e
            if attempt < retries:
                log.warn(f"Network error on attempt {attempt}/{retries} - retrying")
                _backoff(attempt)
                continue
            raise
    log.error(f"Failed to fetch URL after {retries} attempts: {repr(last_err)}")
    raise last_err

def _backoff(attempt: int):
    time.sleep(DEFAULT_BACKOFF_SECONDS * (2 ** (attempt - 1)))

def parse_and_index(m3u_bytes: bytes, filter_fn=None, drop_vod: bool = True) -> dict:
    text = m3u_bytes.decode('utf-8', errors='replace')
    f = io.StringIO(text)
    channels = []
    groups = {}
    for ch in iter_m3u(f, filter_fn=filter_fn, drop_vod=drop_vod):
        idx = len(channels)
        channels.append(ch)
        g = ch.get('group') or 'Ungrouped'
        groups.setdefault(g, []).append(idx)
    log.debug(f"Index built: channels={len(channels)}, groups={len(groups)}")
    return {'channels': channels, 'groups': groups}
