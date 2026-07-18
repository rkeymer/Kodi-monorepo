# -*- coding: utf-8 -*-
import json
import os
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

import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()

# Some providers reject unrecognised User-Agents (e.g. a generic VLC UA) with a
# blanket 401 across every stream while an allowlisted player UA goes through
# fine on the same account - see settings.xml's livetv_user_agent for detail.
DEFAULT_UA = 'IPTVSmartersPro'


def get_ua() -> str:
    return ADDON.getSetting('livetv_user_agent') or DEFAULT_UA


DEFAULT_BACKOFF_SECONDS = 2


def fetch_url(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    headers = {'User-Agent': get_ua(), 'Accept': '*/*', 'Accept-Encoding': 'gzip', 'Connection': 'close'}
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


def check_stream_ok(url: str, timeout: int = 4) -> bool:
    """Pre-flight probe for a live stream URL. Issues a GET (matching what real
    playback does — this provider's own Stat/HEAD-style check has been seen to
    behave differently to a GET) and closes the response as soon as headers come
    back, without reading the body — it's a live TV stream, so reading it would
    block/download indefinitely rather than complete.
    """
    headers = {'User-Agent': get_ua(), 'Accept': '*/*'}
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        try:
            status = getattr(resp, 'status', 200)
            return 200 <= status < 400
        finally:
            resp.close()
    except Exception as e:
        _log(f"check_stream_ok failed for {url}: {repr(e)}")
        return False


def _backoff(attempt: int):
    time.sleep(DEFAULT_BACKOFF_SECONDS * (2 ** (attempt - 1)))


def _read_meta(meta_path: str) -> dict:
    if not meta_path or not xbmcvfs.exists(meta_path):
        return {}
    f = xbmcvfs.File(meta_path)
    try:
        raw = f.read()
    finally:
        f.close()
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _write_meta(meta_path: str, data: dict):
    if not meta_path:
        return
    parent = os.path.dirname(meta_path)
    if parent and not xbmcvfs.exists(parent):
        xbmcvfs.mkdirs(parent)
    f = xbmcvfs.File(meta_path, 'w')
    try:
        f.write(json.dumps(data))
    finally:
        f.close()


def download_to_file(url: str, dest_path: str, meta_path: str = None, use_conditional: bool = True, timeout: int = 90):
    """Stream a URL to disk, with optional ETag/Last-Modified conditional GET.

    Used for the EPG (XMLTV) download, which can be large — avoids re-downloading
    unchanged data on every refresh.
    """
    headers = {'User-Agent': get_ua(), 'Accept-Encoding': 'gzip'}
    meta = _read_meta(meta_path) if (meta_path and use_conditional) else {}
    if meta.get('etag'):
        headers['If-None-Match'] = meta['etag']
    if meta.get('last_modified'):
        headers['If-Modified-Since'] = meta['last_modified']
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=timeout)
    status = getattr(resp, 'status', 200)
    encoding = resp.headers.get('Content-Encoding', '')
    if encoding.lower() == 'gzip':
        import gzip
        stream = gzip.GzipFile(fileobj=resp)
    else:
        stream = resp
    parent = os.path.dirname(dest_path)
    if parent and not xbmcvfs.exists(parent):
        xbmcvfs.mkdirs(parent)
    real_path = xbmcvfs.translatePath(dest_path)
    with open(real_path, 'wb') as out:
        while True:
            chunk = stream.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    if meta_path and use_conditional:
        new_meta = {'etag': resp.headers.get('ETag', ''), 'last_modified': resp.headers.get('Last-Modified', '')}
        _write_meta(meta_path, new_meta)
    return True, status
