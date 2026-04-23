import sys
import urllib.parse
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
from datetime import datetime, timezone, date

from resources.lib.simkl_api import SimklApi
from resources.lib.tmdb_api import TmdbApi  # TMDB client module



ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo("path")
MEDIA_PATH = f"{ADDON_PATH}/resources/media"


HANDLE = int(sys.argv[1])

# --------------------------
# Logging / utility
# --------------------------
def log(msg):
    xbmc.log(f"[SIMKL Watching] {msg}", xbmc.LOGINFO)


def get_params():
    """
    sys.argv[2] contains the querystring portion passed by Kodi, e.g.
    '?action=new'
    """
    if len(sys.argv) < 3 or not sys.argv[2]:
        return {}
    return dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?')))


def build_url(**kwargs):
    """
    Builds a plugin URL like:
    plugin://plugin.video.simkl.watching/?action=new
    """
    return sys.argv[0] + "?" + urllib.parse.urlencode(kwargs)


def add_folder(label, action, icon=None):
    li = xbmcgui.ListItem(label=label)
    if icon:
        li.setArt({"icon": icon, "thumb": icon})
    url = build_url(action=action)
    xbmcplugin.addDirectoryItem(
        handle=HANDLE,
        url=url,
        listitem=li,
        isFolder=True
    )


def add_item(label, url="", info=None, art=None, is_folder=False):
    li = xbmcgui.ListItem(label=label)
    if info:
        li.setInfo("video", info)
    if art:
        li.setArt(art)
    xbmcplugin.addDirectoryItem(
        handle=HANDLE,
        url=url,
        listitem=li,
        isFolder=is_folder
    )


def end_dir():
    xbmcplugin.endOfDirectory(HANDLE)


def parse_sxxexx(s):
    # "S02E05" -> (2,5)
    if not s:
        return None
    try:
        u = s.strip().upper()
        if not (u.startswith("S") and "E" in u):
            return None
        a, b = u[1:].split("E", 1)
        return int(a), int(b)
    except Exception:
        return None


def _parse_iso_utc_datetime(s):
    """
    Best-effort parse SIMKL timestamps like:
      - 2026-03-03T20:55:27Z
      - 2026-03-03T20:55:27.000Z
    Returns datetime (naive UTC) or None.
    """
    if not s:
        return None
    try:
        txt = str(s).strip()
        if txt.endswith("Z"):
            # remove milliseconds if present
            if "." in txt:
                txt = txt.split(".", 1)[0] + "Z"
            return datetime.strptime(txt, "%Y-%m-%dT%H:%M:%SZ")
        # fallback: take first 19 chars "YYYY-MM-DDTHH:MM:SS"
        if "T" in txt and len(txt) >= 19:
            return datetime.strptime(txt[:19], "%Y-%m-%dT%H:%M:%S")
        return None
    except Exception:
        return None


def _parse_ymd_date(s):
    """
    Parse YYYY-MM-DD to date or None.
    """
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


# --------------------------
# Local-date countdown helpers (Upcoming list)
# --------------------------
def _today_local_date():
    # "from today" should be based on the Kodi machine's local date
    return date.today()


def _days_until(iso_yyyy_mm_dd):
    """
    Returns number of days until given YYYY-MM-DD date string.
    - None if missing/invalid.
    - Negative if in the past (we keep it in Upcoming, but label becomes title-only).
    """
    if not iso_yyyy_mm_dd:
        return None
    try:
        target = datetime.strptime(iso_yyyy_mm_dd, "%Y-%m-%d").date()
        return (target - _today_local_date()).days
    except Exception:
        return None


# --------------------------
# Homelander integration
# --------------------------
def build_homelander_url(action, imdb, tmdb, tvshowtitle, year, season=None):
    """
    Builds a Homelander navigation URL based on what you captured in kodi.log
    """
    params = {
        "action": action,
        "imdb": imdb or "",
        "tmdb": tmdb or "",
        "tvshowtitle": tvshowtitle or "",
        "year": str(year or ""),
    }
    if season is not None:
        params["season"] = str(season)
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"plugin://plugin.video.homelander/?{qs}"


def focus_episode_in_ui(target_episode, delay_before=1.2, delay_between=0.06):
    """
    Best-effort: waits briefly for Homelander to render the episode list,
    then sends 'Down' actions (episode-1 times) to highlight the target episode.
    """
    try:
        monitor = xbmc.Monitor()
        monitor.waitForAbort(delay_before)
        steps = max(0, int(target_episode) - 1)
        for _ in range(steps):
            xbmc.executebuiltin("Action(Down)")
            monitor.waitForAbort(delay_between)
    except Exception as e:
        xbmc.log(f"[SIMKL Watching] focus_episode_in_ui error: {e}", xbmc.LOGERROR)


def open_homelander(params):
    title = params.get("title", "")
    imdb = params.get("imdb", "")
    tmdb = params.get("tmdb", "")
    year = params.get("year", "")
    nxt = params.get("next", "")  # e.g. "S01E03" (may be empty)

    season = None
    episode = None
    se = parse_sxxexx(nxt)
    if se:
        season, episode = se

    if season is not None:
        homelander_url = build_homelander_url("episodes", imdb, tmdb, title, year, season=season)
    else:
        homelander_url = build_homelander_url("seasons", imdb, tmdb, title, year)

    xbmc.executebuiltin(f'ActivateWindow(10025,"{homelander_url}",return)')

    if episode is not None and season is not None:
        focus_episode_in_ui(episode, delay_before=1.2, delay_between=0.06)
    return


# --------------------------
# SIMKL posters
# --------------------------
def simkl_poster_url(poster_path, size_suffix="_m", ext=".webp"):
    """
    Build a SIMKL poster URL using SIMKL's recommended proxy and simkl.in domain.
    """
    if not poster_path:
        return None
    return f"https://wsrv.nl/?url=https://simkl.in/posters/{poster_path}{size_suffix}{ext}"


# --------------------------
# TMDB airdate helpers
# --------------------------
def use_tmdb_airdates(addon):
    """
    airdate_source enum in settings:
    0 = SIMKL
    1 = TMDB
    """
    try:
        return addon.getSettingInt("airdate_source") == 1
    except Exception:
        return addon.getSettingString("airdate_source").strip() in ("1", "TMDB", "tmdb")


def tmdb_airdate_for_next(addon, tmdb_id, next_code):
    """
    next_code like "S01E03" -> query TMDB season details -> return YYYY-MM-DD or None.
    """
    if not tmdb_id or not next_code:
        return None
    se = parse_sxxexx(next_code)
    if not se:
        return None
    season, episode = se
    api = TmdbApi(addon)
    if not api.is_configured():
        return None
    try:
        return api.episode_air_date(int(tmdb_id), int(season), int(episode))
    except Exception as e:
        xbmc.log(f"[SIMKL Watching][TMDB] airdate lookup failed: {e}", xbmc.LOGERROR)
        return None


def tmdb_next_episode_airdate(addon, tmdb_id):
    """
    Uses TMDB TV details next_episode_to_air when available.
    """
    if not tmdb_id:
        return None
    api = TmdbApi(addon)
    if not api.is_configured():
        return None
    try:
        return api.next_episode_air_date(int(tmdb_id))
    except Exception as e:
        xbmc.log(f"[SIMKL Watching][TMDB] next_episode lookup failed: {e}", xbmc.LOGERROR)
        return None


# --------------------------
# Screens
# --------------------------
def show_main_menu():
    xbmcplugin.setPluginCategory(HANDLE, "WhatsUpNext")
    add_folder("New Episodes", "new", icon=f"{MEDIA_PATH}/new.png")
    add_folder("Upcoming Episodes", "upcoming", icon=f"{MEDIA_PATH}/upcoming.png")
    add_folder("Authorize SIMKL", "auth", icon=f"{MEDIA_PATH}/auth.png")
    add_folder("Settings / Help", "help", icon=f"{MEDIA_PATH}/help.png")
    end_dir()


def show_new_episodes():
    """
    NEW SORT RULE:
      - Sort by last_watched_at (most recent first).
    """
    xbmcplugin.setPluginCategory(HANDLE, "New Episodes")
    addon = xbmcaddon.Addon()
    api = SimklApi(addon)

    if not api.is_authorized():
        add_item("Not authorized. Run 'Authorize SIMKL' first.")
        end_dir()
        return

    try:
        data = api.get_watching_shows()
        if addon.getSettingBool("debug_logging"):
            xbmc.log(f"[SIMKL Watching] /sync/all-items/shows/watching response: {data}", xbmc.LOGINFO)

        shows = data.get("shows", []) if isinstance(data, dict) else []
        if not shows:
            add_item("No items returned from SIMKL Watching list.")
            end_dir()
            return

        rows = []
        tmdb_enabled = use_tmdb_airdates(addon)

        for it in shows:
            show = it.get("show", {})
            ids = show.get("ids") or {}

            title = show.get("title", "Unknown title")
            year = show.get("year", "")
            imdb_id = ids.get("imdb", "")
            tmdb_id = ids.get("tmdb", "")
            poster_path = show.get("poster")

            next_to_watch = it.get("next_to_watch")  # e.g. "S01E03" or None
            watched = it.get("watched_episodes_count") or 0
            total = it.get("total_episodes_count") or 0
            not_aired = it.get("not_aired_episodes_count") or 0

            aired_total = max(0, total - not_aired)
            new_count = max(0, aired_total - watched)

            if new_count <= 0:
                continue

            oldest = next_to_watch if next_to_watch else "unknown next episode"

            airdate = None
            if tmdb_enabled:
                airdate = tmdb_airdate_for_next(addon, tmdb_id, next_to_watch)

            # Sort key: SIMKL last_watched_at (most recent first)
            last_watched_at = it.get("last_watched_at")
            last_dt = _parse_iso_utc_datetime(last_watched_at)

            rows.append((last_dt, new_count, title, oldest, airdate, poster_path, imdb_id, tmdb_id, year))

        if not rows:
            add_item("You're all caught up ✅ (no aired unwatched episodes).")
            end_dir()
            return

        # Sort by most recently watched (descending). Unknown last_watched_at goes bottom.
        def _sort_key(r):
            last_dt, _, title = r[0], r[1], r[2]
            if last_dt is None:
                return (1, float("inf"), title.lower())
            return (0, -last_dt.timestamp(), title.lower())

        rows.sort(key=_sort_key)

        show_posters = addon.getSettingBool("show_posters")

        for last_dt, new_count, title, oldest, airdate, poster_path, imdb_id, tmdb_id, year in rows:
            if airdate:
                label = f"{title} — {new_count} new — {oldest} ({airdate})"
            else:
                label = f"{title} — {new_count} new — {oldest}"

            art = None
            if show_posters:
                purl = simkl_poster_url(poster_path)
                if purl:
                    art = {"thumb": purl, "poster": purl, "icon": purl}

            url = build_url(
                action="open_homelander",
                title=title,
                imdb=imdb_id,
                tmdb=tmdb_id,
                year=year,
                next=oldest if oldest != "unknown next episode" else ""
            )

            add_item(label, url=url, info={"title": title}, art=art, is_folder=False)

        end_dir()

    except Exception as e:
        xbmc.log(f"[SIMKL Watching] New Episodes failed: {e}", xbmc.LOGERROR)
        add_item("Failed to fetch SIMKL data. Check kodi.log.")
        end_dir()


def show_upcoming():
    """
    NEW SORT RULE:
      - Sort by newest release date (airdate descending).
    """
    xbmcplugin.setPluginCategory(HANDLE, "Upcoming Episodes")
    addon = xbmcaddon.Addon()
    api = SimklApi(addon)

    if not api.is_authorized():
        add_item("Not authorized. Run 'Authorize SIMKL' first.")
        end_dir()
        return

    try:
        data = api.get_watching_shows()
        if addon.getSettingBool("debug_logging"):
            xbmc.log(f"[SIMKL Watching] /sync/all-items/shows/watching response: {data}", xbmc.LOGINFO)

        shows = data.get("shows", []) if isinstance(data, dict) else []
        if not shows:
            add_item("No items returned from SIMKL Watching list.")
            end_dir()
            return

        rows = []
        tmdb_enabled = use_tmdb_airdates(addon)

        for it in shows:
            show = it.get("show", {})
            ids = show.get("ids") or {}

            title = show.get("title", "Unknown title")
            tmdb_id = ids.get("tmdb", "")
            poster_path = show.get("poster")

            watched = it.get("watched_episodes_count") or 0
            total = it.get("total_episodes_count") or 0
            not_aired = it.get("not_aired_episodes_count") or 0

            # Same logic as before:
            aired_total = max(0, total - not_aired)
            new_aired_unwatched = max(0, aired_total - watched)

            # Upcoming shows only: caught up on aired episodes, but has future episodes
            if new_aired_unwatched != 0:
                continue
            if not_aired <= 0:
                continue

            next_to_watch = it.get("next_to_watch") or ""

            airdate = None
            if tmdb_enabled:
                airdate = tmdb_airdate_for_next(addon, tmdb_id, next_to_watch)
                if not airdate:
                    airdate = tmdb_next_episode_airdate(addon, tmdb_id)

            rows.append((not_aired, title, next_to_watch, airdate, poster_path))

        if not rows:
            add_item("No upcoming episodes found (or you have new aired episodes instead).")
            end_dir()
            return

        # Sort by newest known airdate first (descending). Unknown dates go bottom.
        def _sort_key(row):
            _, title, _, airdate, _ = row
            d = _parse_ymd_date(airdate)
            if d is None:
                return (1, 0, title.lower())
            return (0, -d.toordinal(), title.lower())

        rows.sort(key=_sort_key)

        show_posters = addon.getSettingBool("show_posters")

        for _, title, _, airdate, poster_path in rows:
            # Keep your existing label rules/countdown, just change ordering.
            d = _days_until(airdate)

            if d is None:
                label = f"{title}"
            else:
                if d < 0:
                    label = f"{title}"
                elif d == 0:
                    label = f"{title} — today — ({airdate})"
                elif d == 1:
                    label = f"{title} — tomorrow — ({airdate})"
                else:
                    label = f"{title} — {d} days — ({airdate})"

            art = None
            if show_posters:
                url = simkl_poster_url(poster_path)
                if url:
                    art = {"thumb": url, "poster": url, "icon": url}

            add_item(label, info={"title": title}, art=art)

        end_dir()

    except Exception as e:
        xbmc.log(f"[SIMKL Watching] Upcoming failed: {e}", xbmc.LOGERROR)
        add_item("Failed to fetch SIMKL data. Check kodi.log.")
        end_dir()


def show_help():
    xbmcplugin.setPluginCategory(HANDLE, "Settings / Help")
    add_item("Tip: Add TMDB API key in Settings to show episode air dates / countdown.")
    add_item("Debug: Enable 'Debug logging' to log raw SIMKL/TMDB responses.")
    end_dir()


def show_auth():
    addon = xbmcaddon.Addon()
    api = SimklApi(addon)

    if not api.is_configured():
        xbmcgui.Dialog().ok(
            "SIMKL • Watching",
            "No SIMKL Client ID found.\n\n"
            "Go to the add-on Settings and paste your SIMKL Client ID (API Key)."
        )
        show_help()
        return

    try:
        pin = api.request_pin()
    except Exception as e:
        xbmc.log(f"[SIMKL Watching] PIN request failed: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("SIMKL", "PIN request failed", xbmcgui.NOTIFICATION_ERROR)
        show_main_menu()
        return

    user_code = pin.get("user_code")
    verify_url = pin.get("verification_url") or "https://simkl.com/pin/"
    expires_in = int(pin.get("expires_in", 900))
    interval = int(pin.get("interval", 5))

    if not user_code:
        xbmcgui.Dialog().notification("SIMKL", "PIN response missing user_code", xbmcgui.NOTIFICATION_ERROR)
        show_main_menu()
        return

    xbmcgui.Dialog().ok(
        "SIMKL Authorization",
        f"On your phone/PC visit:\n{verify_url}\n\n"
        f"Enter this code:\n\n{user_code}\n\n"
        "Approve access, then return to Kodi."
    )

    dp = xbmcgui.DialogProgress()
    dp.create("SIMKL Authorization", "Waiting for approval…")

    def progress_cb(remaining):
        percent = int((expires_in - remaining) * 100 / expires_in)
        percent = max(0, min(100, percent))
        dp.update(percent, f"Code: {user_code}\nTime left: {remaining}s")
        if dp.iscanceled():
            return

    token = None
    try:
        token = api.poll_pin(user_code, interval, expires_in, progress_cb=progress_cb)
    finally:
        dp.close()

    if token:
        api.save_token(token)
        xbmcgui.Dialog().notification("SIMKL", "Authorized successfully!", xbmcgui.NOTIFICATION_INFO)
    else:
        xbmcgui.Dialog().notification("SIMKL", "Authorization timed out", xbmcgui.NOTIFICATION_ERROR)

    show_main_menu()


# --------------------------
# Aired-episode screen (SIMKL extended=full)
# --------------------------
def _parse_date(d):
    if not d:
        return None
    try:
        if len(d) == 10:
            return datetime.strptime(d, "%Y-%m-%d").date()
        if d.endswith("Z"):
            return datetime.strptime(d, "%Y-%m-%dT%H:%M:%SZ").date()
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _today_utc():
    return datetime.now(timezone.utc).date()


def _flatten_episodes(show_json):
    """
    Turns show['seasons'][...]['episodes'][...] into a flat list.
    """
    out = []
    seasons = show_json.get("seasons") or []
    for s in seasons:
        s_num = s.get("number")
        eps = s.get("episodes") or []
        for e in eps:
            e_num = e.get("number")
            title = e.get("title") or f"Episode {e_num}"
            air = e.get("aired") or e.get("first_aired") or e.get("air_date") or e.get("date")
            out.append({
                "season": s_num,
                "episode": e_num,
                "title": title,
                "air_raw": air
            })
    return out


def show_show_info(action):
    addon = xbmcaddon.Addon()
    api = SimklApi(addon)

    try:
        simkl_id = int(action.split("_", 1)[1])
    except Exception:
        xbmcgui.Dialog().notification("SIMKL", "Invalid show id", xbmcgui.NOTIFICATION_ERROR)
        show_new_episodes()
        return

    try:
        show = api.get_show_details_full(simkl_id)
    except Exception as e:
        xbmc.log(f"[SIMKL Watching] show details failed: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("SIMKL", "Failed to load show details", xbmcgui.NOTIFICATION_ERROR)
        show_new_episodes()
        return

    title = show.get("title") or f"Show {simkl_id}"
    xbmcplugin.setPluginCategory(HANDLE, f"{title} — Aired Episodes")

    episodes = _flatten_episodes(show)
    today = _today_utc()
    aired = []

    for e in episodes:
        d = _parse_date(e.get("air_raw"))
        if d and d <= today:
            aired.append((d, e))

    aired.sort(key=lambda x: (x[0], x[1]["season"] or 0, x[1]["episode"] or 0))

    if not aired:
        add_item("No aired episodes found (or data missing).")
        end_dir()
        return

    for d, e in aired:
        s = e["season"] or 0
        n = e["episode"] or 0
        label = f"S{s:02d}E{n:02d} — {e['title']} ({d.isoformat()})"
        add_item(label, info={"title": e["title"]})

    end_dir()


# --------------------------
# Router
# --------------------------
def router():
    params = get_params()
    action = params.get("action", "root")
    log(f"Action: {action}")

    if action == "root":
        show_main_menu()
    elif action == "new":
        show_new_episodes()
    elif action == "upcoming":
        show_upcoming()
    elif action == "help":
        show_help()
    elif action == "auth":
        show_auth()
    elif action == "open_homelander":
        open_homelander(params)
    elif action.startswith("show_"):
        show_show_info(action)
    else:
        xbmcgui.Dialog().notification(
            "SIMKL Watching",
            f"Unknown action: {action}",
            xbmcgui.NOTIFICATION_ERROR
        )
        show_main_menu()


if __name__ == "__main__":
    router()