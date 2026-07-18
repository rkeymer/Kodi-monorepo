import sys
import time
import os
import json
import threading
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.whatsonstreamer'
ADDON = xbmcaddon.Addon(ADDON_ID)

# Ensure addon root is on sys.path (important when invoked via RunScript).
# All internal imports are package-qualified (resources.lib.X), so this only
# ever adds the addon root — never resources/lib itself, which would shadow
# stdlib modules like `http`.
#
# Deriving this from __file__ rather than ADDON.getAddonInfo('path') matters:
# when Kodi invokes this script via RunScript(special://.../service.py,manual)
# it logs "Script invoked without an addon", and getAddonInfo('path') comes
# back empty in that context — silently, since this was wrapped in a bare
# try/except — leaving resources.lib.* unimportable and every auto-update
# attempt crashing with ModuleNotFoundError.
try:
    _addon_path = os.path.dirname(os.path.abspath(__file__))
    if _addon_path and _addon_path not in sys.path:
        sys.path.insert(0, _addon_path)
except Exception:
    pass

from resources.lib.playlist import build_m3u_url, build_epg_url
from resources.lib.playlist_fetch import fetch_url
from resources.lib import log
from resources.lib import scheduler

SCHEDULE_COUNTDOWN_SECONDS = 15
SCHEDULE_GRACE_SECONDS = 600

RESTART_DELAY_SECONDS = 10
HEALTHY_SECONDS = 60
MAX_RESTART_ATTEMPTS = 3

STALL_POSITION_EPSILON_SECONDS = 1.0

PROFILE_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
CACHE_DIR = os.path.join(PROFILE_DIR, 'cache')

ADDON_DATA_DIR = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.whatsonstreamer')
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
    if not _get_bool('livetv_auto_update_notify', True):
        return
    xbmc.executebuiltin(f'Notification(WhatsOnStreamer,{msg},4000,{"info" if ok else "error"})')


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
    base = _get('iptv_base_url')
    user = _get('iptv_username')
    pw = _get('iptv_password')

    local_pl = _get('livetv_local_playlist_path')
    local_epg = _get('livetv_local_epg_path')

    if not (local_pl and local_epg):
        log.warn('Auto-update skipped: missing local paths')
        return False

    _ensure_dir(CACHE_DIR)

    # Local debug mode
    if _get_bool('livetv_use_local_cache_files', False):
        folder = _get('livetv_local_cache_base_path', '')
        pl_src = os.path.join(folder, _get('livetv_local_cache_playlist_file', 'playlist-online.m3u'))
        epg_src = os.path.join(folder, _get('livetv_local_cache_epg_file', 'epg-online.xml'))
        log.info('AUTOUPDATE mode=LOCAL_CACHE playlist_src=%s epg_src=%s' % (pl_src, epg_src))
        _notify('Auto-update (local cache) started...', True)
        _write_bytes(local_pl, _read_local_file(pl_src, 'playlist'), 'playlist')
        _write_bytes(local_epg, _read_local_file(epg_src, 'epg'), 'epg')
        _notify('Auto-update (local cache) completed', True)
        return True

    if not (base and user and pw):
        log.warn('Auto-update skipped: missing credentials')
        return False

    m3u_url = build_m3u_url(base, _get('livetv_m3u_path', '/get.php'), user, pw,
                            _get('livetv_m3u_type', 'm3u_plus'), _get('livetv_output', 'ts'))
    epg_url = build_epg_url(base, _get('livetv_epg_path', '/xmltv.php'), user, pw)

    log.info('AUTOUPDATE mode=WEB urls m3u=%s epg=%s' % (m3u_url, epg_url))
    _notify('Auto-update started...', True)

    timeout_s = _get_int('livetv_playlist_timeout', 600)
    retries = _get_int('livetv_playlist_retries', 3)
    epg_timeout = _get_int('livetv_epg_timeout', 600)

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
    """Run a single update immediately (invoked from the root Tools menu)."""
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


def _warm_watching_shows(simkl, tmdb) -> int:
    """TmdbApi.tv_details() per watched show - what New Episodes/Upcoming loop
    on a cold cache. Mirrors main.py's show_new_episodes()/show_upcoming()."""
    try:
        data = simkl.get_watching_shows()
    except Exception as e:
        log.warn('WhatsUpNext cache warm: SIMKL shows fetch failed: %s' % repr(e))
        return 0

    shows = data.get('shows', []) if isinstance(data, dict) else []
    warmed = 0
    for it in shows:
        tmdb_id = ((it.get('show') or {}).get('ids') or {}).get('tmdb')
        if not tmdb_id:
            continue
        try:
            tmdb.tv_details(int(tmdb_id))
            warmed += 1
        except Exception as e:
            log.warn('WhatsUpNext cache warm: TMDB tv_details failed for %s: %s' % (tmdb_id, repr(e)))
    return warmed


def _warm_plan_movies(simkl, tmdb) -> int:
    """TmdbApi.movie_details() per SIMKL Plan-to-Watch movie - what
    show_movies() loops on a cold cache. Deliberately skips replicating
    show_movies()'s status/shape defensive parsing in full (risks drift) -
    just normalizes the common {movies|items|data: [...]}/bare-list shapes
    and warms every item with a tmdb id; harmless if that's a superset of
    what show_movies() ultimately displays."""
    try:
        data = simkl.get_plan_movies()
    except Exception as e:
        log.warn('WhatsUpNext cache warm: SIMKL movies fetch failed: %s' % repr(e))
        return 0

    movies = []
    if isinstance(data, dict):
        for key in ('movies', 'items', 'data'):
            if isinstance(data.get(key), list):
                movies = data[key]
                break
    elif isinstance(data, list):
        movies = data

    warmed = 0
    for it in movies:
        if not isinstance(it, dict):
            continue
        movie = it.get('movie') or it.get('film') or it
        if not isinstance(movie, dict):
            continue
        tmdb_id = (movie.get('ids') or {}).get('tmdb')
        if not tmdb_id:
            continue
        try:
            tmdb.movie_details(int(tmdb_id))
            warmed += 1
        except Exception as e:
            log.warn('WhatsUpNext cache warm: TMDB movie_details failed for %s: %s' % (tmdb_id, repr(e)))
    return warmed


def _warm_recommendations(simkl, tmdb) -> tuple:
    """Builds and saves the "Recommended" shows/movies lists (resources/lib/recommendations.py)
    from the account's SIMKL watch history, so the Recommended screen is always a fast cache
    read in the foreground rather than a ~150-item live sweep. Returns (num_shows, num_movies).
    `tmdb` is passed through so recommendations.build() can apply its English-only filter."""
    from resources.lib import recommendations

    data = _safe_call(lambda: recommendations.build(simkl, tmdb), 'Recommendations build')
    if not data:
        return (0, 0)
    recommendations.save(data)
    return (len(data.get('shows') or []), len(data.get('movies') or []))


def _safe_call(fn, label):
    try:
        return fn()
    except Exception as e:
        log.warn('%s failed: %s' % (label, repr(e)))
        return None


def _warm_whatsupnext_cache(force: bool = False):
    """Pre-fetches TmdbApi.tv_details()/movie_details() for every currently
    watching show and planned movie, so New Episodes/Upcoming Episodes/Movies
    never hit a cold TMDB cache in the foreground (that per-item synchronous
    lookup loop, done inline in main.py on a cold cache, is what makes first
    open slow). Both TMDB calls already check their own disk cache before
    making a network call, so running this often costs nothing once warm.
    Also (re)builds the Recommended lists (resources/lib/recommendations.py)
    on the same schedule, for the same reason.

    force=True (the manual Tools-menu trigger) mirrors run_manual()/_do_update()
    above: skips both the "disabled" and the last-run gates entirely, same as
    that button ignores livetv_auto_update_enabled.
    """
    if not force:
        interval_h = _get_int('whatsupnext_cache_warm_hours', 12)
        if interval_h <= 0:
            return
        st = _load_state()
        now_ts = int(time.time())
        last_ts = int(st.get('last_whatsupnext_warm', 0) or 0)
        if last_ts > 0 and (now_ts - last_ts) < interval_h * 3600:
            return

    from resources.lib.simkl_api import SimklApi
    from resources.lib.tmdb_api import TmdbApi

    simkl = SimklApi(ADDON)
    if not simkl.is_authorized():
        if force:
            xbmc.executebuiltin('Notification(WhatsOnStreamer,SIMKL not authorized - nothing to warm,4000,warning)')
        return

    tmdb = TmdbApi(ADDON)
    if not tmdb.is_configured():
        if force:
            xbmc.executebuiltin('Notification(WhatsOnStreamer,No TMDB API key set - nothing to warm,4000,warning)')
        return

    if force:
        # This can take 30-40s+ (recommendations.build() sweeps ~150 seed items on a
        # cold cache) - without an upfront notification the manual Tools button looks
        # unresponsive until the completion toast fires at the very end.
        xbmc.executebuiltin('Notification(WhatsOnStreamer,Warming WhatsUpNext caches...,3000,info)')

    warmed_shows = _warm_watching_shows(simkl, tmdb)
    warmed_movies = _warm_plan_movies(simkl, tmdb)
    rec_shows, rec_movies = _warm_recommendations(simkl, tmdb)

    st = _load_state()
    st['last_whatsupnext_warm'] = int(time.time())
    _save_state(st)

    log.info('WhatsUpNext cache warm: refreshed %d shows, %d movies, %d/%d recommendations' % (
        warmed_shows, warmed_movies, rec_shows, rec_movies))
    if force:
        xbmc.executebuiltin('Notification(WhatsOnStreamer,Warmed %d shows / %d movies / %d+%d recs,4000,info)' % (
            warmed_shows, warmed_movies, rec_shows, rec_movies))


def _with_auto_flag(plugin_url: str) -> str:
    """Marks a plugin:// re-invocation as background-triggered (watchdog restart,
    scheduled switch) rather than a direct user click, so livetv.py's
    _resolve_channel_playback skips the interactive failover picker - a blocking
    select dialog has no one to answer it when this fires unattended."""
    parts = urllib.parse.urlsplit(plugin_url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != 'auto']
    q.append(('auto', '1'))
    new_query = urllib.parse.urlencode(q)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _play_channel_url(url: str, name: str):
    plugin_url = 'plugin://%s/?action=livetv_play_url&u=%s&n=%s&auto=1' % (
        ADDON_ID, urllib.parse.quote(url or '', safe=''), urllib.parse.quote(name or '', safe=''))
    xbmc.executebuiltin('PlayMedia(%s)' % plugin_url)


def _check_scheduled_events(monitor):
    now = int(time.time())
    due = scheduler.due_events(now, grace_seconds=SCHEDULE_GRACE_SECONDS)

    for ev in due:
        # Mark notified immediately - whatever happens with the countdown below
        # (switch, cancel, or Kodi shutting down mid-countdown), this must not
        # fire again on the next loop tick.
        scheduler.mark_notified(ev.get('id'))

        channel_name = ev.get('channel_name') or 'Channel'
        title = ev.get('title') or '(untitled)'

        dialog = xbmcgui.DialogProgress()
        dialog.create('WhatsOnStreamer', 'Switching to %s' % channel_name)
        cancelled = False
        try:
            for i in range(SCHEDULE_COUNTDOWN_SECONDS):
                if dialog.iscanceled():
                    cancelled = True
                    break
                remaining = SCHEDULE_COUNTDOWN_SECONDS - i
                pct = int((i * 100) / SCHEDULE_COUNTDOWN_SECONDS)
                dialog.update(pct, 'Switching to %s in %ds\n%s\nSelect Cancel to stay on the current channel' % (
                    channel_name, remaining, title))
                if monitor.waitForAbort(1):
                    cancelled = True
                    break
        finally:
            dialog.close()

        if not cancelled:
            log.info('Scheduled switch firing: %s / %s' % (channel_name, title))
            _play_channel_url(ev.get('channel_url', ''), channel_name)
        else:
            log.info('Scheduled switch cancelled: %s / %s' % (channel_name, title))

    scheduler.prune_expired(now)


class LiveTVWatchdog(xbmc.Player):
    """Detects a Live TV channel dropping mid-playback and automatically
    restarts the same channel. Two independent detection paths, since a stream
    can fail in two different ways:

    1. Reactive - Kodi's player itself notices the stream ended/errored (stream
       stalls and Kodi treats it as EOF and closes it - see service.py history
       for the incident that motivated this) and fires onPlayBackEnded/
       onPlayBackError.
    2. Active - check_stall(), polled once per run_loop tick, catches a stream
       that goes silently dead without Kodi's player ever noticing (connection
       stops delivering data without a clean close, so neither callback above
       ever fires - observed 2026-07-10, 40+ minutes frozen with zero events).

    Deliberately does NOT react to onPlayBackStopped - that fires when the
    user stops or switches channel on purpose, and must never trigger a
    restart. Only onPlayBackEnded/onPlayBackError/check_stall's own detection
    (an unexpected end/freeze while a Live TV channel was being tracked) count
    as a drop.

    Identifying "a Live TV channel is playing" can't rely on
    Player.getPlayingFile() alone - for resolved plugin content that reports
    whatever Kodi's video player considers the active file, which in testing
    turned out to be the resolved stream path, not the plugin:// URL originally
    requested (the watchdog silently never matched anything until this was
    fixed). Instead, livetv.py's _resolve_channel_playback publishes both the
    plugin:// URL and the raw stream URL as Window(10000) properties right
    before resolving - readable cross-process within this Kodi instance -
    and getPlayingFile() is matched against whichever one it turns out to be.
    """

    PROP_PLUGIN_URL = 'WhatsOnStreamer.livetv.plugin_url'
    PROP_STREAM_URL = 'WhatsOnStreamer.livetv.stream_url'

    def __init__(self):
        super().__init__()
        self._file = None
        self._started_at = 0
        self._fail_counts = {}
        self._last_stall_check_pos = None

    def onAVStarted(self):
        try:
            f = self.getPlayingFile()
        except Exception:
            return

        win = xbmcgui.Window(10000)
        plugin_url = win.getProperty(self.PROP_PLUGIN_URL)
        stream_url = win.getProperty(self.PROP_STREAM_URL)

        if plugin_url and (f == plugin_url or (stream_url and stream_url in f)):
            self._file = plugin_url
            self._started_at = time.time()
        else:
            self._file = None
        self._last_stall_check_pos = None

    def onPlayBackStopped(self):
        self._file = None
        self._last_stall_check_pos = None

    def onPlayBackEnded(self):
        self._handle_drop()

    def onPlayBackError(self):
        self._handle_drop()

    def _handle_drop(self):
        f = self._file
        if not f:
            return
        self._file = None

        if time.time() - self._started_at >= HEALTHY_SECONDS:
            self._fail_counts[f] = 0
        count = self._fail_counts.get(f, 0) + 1
        self._fail_counts[f] = count

        if count > MAX_RESTART_ATTEMPTS:
            log.warn('Live TV watchdog: giving up on %s after %d attempts' % (f, count - 1))
            self._fail_counts[f] = 0
            xbmc.executebuiltin('Notification(WhatsOnStreamer,Channel kept dropping - giving up auto-reconnect,4000,warning)')
            return

        log.info('Live TV watchdog: unexpected drop (attempt %d/%d), restarting %s' % (count, MAX_RESTART_ATTEMPTS, f))
        threading.Thread(target=self._restart, args=(f, count), daemon=True).start()

    def _restart(self, f: str, attempt: int):
        # Escalating backoff, not a flat delay - some IPTV providers only allow one
        # active connection per account and enforce a cooldown after a disconnect
        # before accepting a new one. A fast flat retry can land inside that
        # cooldown on every attempt, making a single transient drop look like a
        # total outage.
        xbmc.sleep(RESTART_DELAY_SECONDS * attempt * 1000)
        xbmc.executebuiltin('PlayMedia(%s)' % _with_auto_flag(f))

    def check_stall(self):
        """Called once per run_loop tick (~60s). Catches a silently-stalled Live TV
        stream that never fires onPlayBackEnded/onPlayBackError - Kodi's player can
        sit on a frozen last frame indefinitely if the underlying connection stops
        delivering data without a clean close, rather than treating it as EOF/error
        (observed 2026-07-10: 40+ minutes of dead air, zero player-level events, on
        a different addon's stream - but nothing in Kodi's player model makes this
        addon's own streams immune to the same failure mode).

        Detection: if playback position hasn't advanced since the last tick despite
        the player still reporting "playing", the stream is frozen. Skips paused
        playback (Player.Paused - a deliberate user pause also holds position, and
        must never be mistaken for a stall). Reuses _handle_drop()'s existing
        retry-limit/backoff so a stall-then-recover channel doesn't loop forever.
        """
        if not self._file:
            self._last_stall_check_pos = None
            return
        try:
            if not self.isPlaying() or xbmc.getCondVisibility('Player.Paused'):
                self._last_stall_check_pos = None
                return
            pos = self.getTime()
        except Exception:
            self._last_stall_check_pos = None
            return

        if self._last_stall_check_pos is not None and abs(pos - self._last_stall_check_pos) < STALL_POSITION_EPSILON_SECONDS:
            log.warn('Live TV watchdog: playback position frozen at %.1fs - treating as a stall' % pos)
            self._last_stall_check_pos = None
            self._handle_drop()
            return

        self._last_stall_check_pos = pos


def run_loop():
    monitor = xbmc.Monitor()
    watchdog = LiveTVWatchdog()  # noqa: F841 - kept alive for its Player callbacks
    log.info('Service started')
    monitor.waitForAbort(15)

    while not monitor.abortRequested():
        try:
            if _get_bool('livetv_auto_update_enabled', False):
                mode = _get('livetv_auto_update_mode', '1')  # 0=daily, 1=interval
                st = _load_state()

                if str(mode) == '0':
                    hh, mm = _parse_hhmm(_get('livetv_auto_update_time', '02:00'))
                    lt = time.localtime(time.time())
                    today = f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}"
                    last = st.get('last_service_update_date', '')
                    if last != today and (lt.tm_hour, lt.tm_min) >= (hh, mm):
                        if _do_update():
                            st['last_service_update_date'] = today
                            st['last_auto_update'] = int(time.time())
                            _save_state(st)
                else:
                    interval_h = _get_int('livetv_auto_update_interval_hours', 6)
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

        try:
            watchdog.check_stall()
        except Exception as e:
            log.warn('Live TV watchdog stall check error: %s' % repr(e))

        try:
            _warm_whatsupnext_cache()
        except Exception as e:
            log.warn('WhatsUpNext cache warm error: %s' % repr(e))

        try:
            _check_scheduled_events(monitor)
        except Exception as e:
            log.warn('Scheduled-events check error: %s' % repr(e))

        if monitor.waitForAbort(60):
            break

    log.info('Service stopped')


if __name__ == '__main__':
    # If invoked via RunScript(...,manual) or (...,warm_whatsupnext) run once and exit
    _args_lower = [str(a).lower() for a in sys.argv[1:]] if len(sys.argv) > 1 else []
    if 'manual' in _args_lower:
        run_manual()
    elif 'warm_whatsupnext' in _args_lower:
        _warm_whatsupnext_cache(force=True)
    else:
        run_loop()
