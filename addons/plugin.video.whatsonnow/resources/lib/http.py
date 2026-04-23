import json
import os
import urllib.request
import xbmcvfs

UA = 'WhatsOnNow/1.0 (Kodi)'

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
    headers = {'User-Agent': UA, 'Accept-Encoding': 'gzip'}
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
