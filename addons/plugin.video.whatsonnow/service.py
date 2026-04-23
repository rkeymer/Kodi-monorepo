import sys
import time
import os
import json

import xbmc
import xbmcaddon
import xbmcvfs

ADDON_ID = 'plugin.video.whatsonnow'
ADDON = xbmcaddon.Addon(ADDON_ID)

# Ensure addon root is on sys.path (important when invoked via RunScript)
# NOTE: Do NOT add resources/lib directly to sys.path, as it can shadow stdlib modules
# (e.g., resources/lib/http.py would shadow Python's stdlib 'http' package).
try:
    _addon_path = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
    if _addon_path and _addon_path not in sys.path:
        sys.path.insert(0, _addon_path)
except Exception:
    pass

from resources.lib.playlist import build_m3u_url, build_epg_url
from resources.lib.playlist_fetch import fetch_url
from resources.lib import log

PROFILE_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
CACHE_DIR = os.path.join(PROFILE_DIR, 'cache')

ADDON_DATA_DIR = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.whatsonnow')
STATE_PATH = os.path.join(ADDON_DATA_DIR, 'update_state.json')


def _ensure_dir(p: str):
    if p and not os.path.exists(p):
        os.makedirs(p, exist_ok=True)


def _load_state() -> dict:
    _ensure_dir(ADDON_DATA_DIR)
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(st: dict):
    _ensure_dir(ADDON_DATA_DIR)
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(st or {}, f, ensure_ascii=False)


def _get(id_, default=''):
    v = ADDON.getSetting(id_)
    return v if v != '' else default


def _get_int(id_, default=0) -> int:
    try:
        return int(float(_get(id_, str(default)) or default))
    except Exception:
        return default


def _get_bool(id_, default=False) -> bool:
    try:
        return str(_get(id_, 'true' if default else 'false')).lower() == 'true'
    except Exception:
        return default


def _parse_hhmm(s: str):
    try:
        hh, mm = (s or '02:00').strip().split(':', 1)
        return int(hh), int(mm)
    except Exception:
        return 2, 0


def _notify(msg: str, ok: bool = True):
    if not _get_bool('auto_update_notify', True):
        return
    xbmc.executebuiltin(f'Notification(WhatsOnNow,{msg},4000,{"info" if ok else "error"})')


def _write_bytes(dest_path: str, data: bytes, label: str):
    real = xbmcvfs.translatePath(dest_path)
    parent = os.path.dirname(real)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    total = len(data)
    log.info(f"WRITE start {label} bytes={total} path={dest_path} real={real}")

    step = 5 * 1024 * 1024
    written = 0

    with open(real, 'wb') as out:
        for off in range(0, total, step):
            chunk = data[off:off + step]
            out.write(chunk)
            written += len(chunk)
            out.flush()
            try:
                os.fsync(out.fileno())
            except Exception:
                pass
            if total > 0:
                pct = int((written * 100) / total)
                log.info(f"WRITE progress {label} {written}/{total} ({pct}%)")

    log.info(f"WRITE done {label} bytes={written} path={dest_path}")


def _read_local_file(path: str, label: str) -> bytes:
    if not path:
        raise Exception(f"Local cache path not set for {label}")
    if not os.path.exists(path):
        raise Exception(f"Local cache file missing for {label}: {path}")
    sz = os.path.getsize(path)
    if sz <= 0:
        raise Exception(f"Local cache file is empty for {label}: {path}")
    log.info(f"LOCAL read {label} path={path} bytes={sz}")
    with open(path, 'rb') as f:
        data = f.read()
    if not data:
        raise Exception(f"Local cache read returned empty for {label}: {path}")
    return data


def _do_update() -> bool:
    base = _get('base_url')
    user = _get('username')
    pw = _get('password')

    local_pl = _get('local_playlist_path')
    local_epg = _get('local_epg_path')

    if not (local_pl and local_epg):
        log.warn('Auto-update skipped: missing local paths')
        return False

    _ensure_dir(CACHE_DIR)

    # Local debug mode
    if _get_bool('use_local_cache_files', False):
        folder = _get('local_cache_base_path', '')
        pl_src = os.path.join(folder, _get('local_cache_playlist_file', 'playlist-online.m3u'))
        epg_src = os.path.join(folder, _get('local_cache_epg_file', 'epg-online.xml'))
        log.info('AUTOUPDATE mode=LOCAL_CACHE playlist_src=%s epg_src=%s' % (pl_src, epg_src))
        _notify('Auto-update (local cache) started...', True)
        _write_bytes(local_pl, _read_local_file(pl_src, 'playlist'), 'playlist')
        _write_bytes(local_epg, _read_local_file(epg_src, 'epg'), 'epg')
        _notify('Auto-update (local cache) completed', True)
        return True

    if not (base and user and pw):
        log.warn('Auto-update skipped: missing credentials')
        return False

    m3u_url = build_m3u_url(base, _get('m3u_path', '/get.php'), user, pw,
                            _get('m3u_type', 'm3u_plus'), _get('output', 'ts'))
    epg_url = build_epg_url(base, _get('epg_path', '/xmltv.php'), user, pw)

    log.info('AUTOUPDATE mode=WEB urls m3u=%s epg=%s' % (m3u_url, epg_url))
    _notify('Auto-update started...', True)

    timeout_s = _get_int('playlist_timeout', 600)
    retries = _get_int('playlist_retries', 3)
    epg_timeout = _get_int('epg_timeout', 600)

    try:
        log.info(f"FETCH playlist start timeout={timeout_s}s retries={retries}")
        data = fetch_url(m3u_url, timeout=timeout_s, retries=retries)
        log.info(f"FETCH playlist done bytes={len(data) if data else 0}")
        if not data:
            raise Exception('Playlist fetch returned empty')
        _write_bytes(local_pl, data, 'playlist')

        log.info(f"FETCH epg start timeout={epg_timeout}s")
        epg_data = fetch_url(epg_url, timeout=epg_timeout, retries=2)
        log.info(f"FETCH epg done bytes={len(epg_data) if epg_data else 0}")
        if not epg_data:
            raise Exception('EPG fetch returned empty')
        _write_bytes(local_epg, epg_data, 'epg')

        _notify('Auto-update completed', True)
        return True
    except Exception as e:
        log.error('Auto-update failed: %s' % repr(e))
        _notify('Auto-update failed', False)
        return False


def run_manual() -> bool:
    """Run a single update immediately (invoked from Tools/Diagnostics)."""
    try:
        st = _load_state()
        ok = _do_update()
        if ok:
            now_ts = int(time.time())
            st['last_manual_update'] = now_ts
            st['last_auto_update'] = now_ts
            _save_state(st)
        return ok
    except Exception as e:
        log.error('Manual update failed: %s' % repr(e))
        return False


def run_loop():
    monitor = xbmc.Monitor()
    log.info('Service started')
    monitor.waitForAbort(15)

    while not monitor.abortRequested():
        try:
            if _get_bool('auto_update_enabled', False):
                mode = _get('auto_update_mode', '1')  # 0=daily, 1=interval
                st = _load_state()

                if str(mode) == '0':
                    hh, mm = _parse_hhmm(_get('auto_update_time', '02:00'))
                    lt = time.localtime(time.time())
                    today = f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}"
                    last = st.get('last_service_update_date', '')
                    if last != today and (lt.tm_hour, lt.tm_min) >= (hh, mm):
                        if _do_update():
                            st['last_service_update_date'] = today
                            st['last_auto_update'] = int(time.time())
                            _save_state(st)
                else:
                    interval_h = _get_int('auto_update_interval_hours', 6)
                    try:
                        interval_h = int(interval_h)
                    except Exception:
                        interval_h = 6
                    interval_h = max(1, interval_h)

                    now_ts = int(time.time())
                    last_ts = int(st.get('last_auto_update', 0) or 0)
                    if last_ts <= 0 or (now_ts - last_ts) >= (interval_h * 3600):
                        if _do_update():
                            st['last_auto_update'] = now_ts
                            _save_state(st)

        except Exception as e:
            log.warn('Service loop error: %s' % repr(e))

        if monitor.waitForAbort(60):
            break

    log.info('Service stopped')


if __name__ == '__main__':
    # If invoked via RunScript(...,manual) run once and exit
    if len(sys.argv) > 1 and any(str(a).lower() == 'manual' for a in sys.argv[1:]):
        run_manual()
    else:
        run_loop()
