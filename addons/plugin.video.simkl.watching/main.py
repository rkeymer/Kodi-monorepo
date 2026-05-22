import sys
import urllib.parse
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
from datetime import datetime, timezone, date

from resources.lib.simkl_api import SimklApi
from resources.lib.tmdb_api import TmdbApi
from resources.lib.alldebrid_api import AllDebridApi



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


def add_item(label, url="", info=None, art=None, is_folder=False, context_menu=None, label2=""):
    li = xbmcgui.ListItem(label=label, label2=label2)
    if info:
        li.setInfo("video", info)
    if art:
        li.setArt(art)
    if context_menu:
        li.addContextMenuItems(context_menu)
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
    Builds a Homelander navigation URL based on what you captured in kodi.log.
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
    media_type = params.get("media_type", "tv")

    if media_type == "movie":
        name = f"{title} ({year})" if year else title
        homelander_url = (
            "plugin://plugin.video.homelander/?action=movieSearchterm&name="
            + urllib.parse.quote(name)
        )
        xbmc.executebuiltin(f'ActivateWindow(10025,"{homelander_url}",return)')
        return

    season = None
    episode = None
    se = parse_sxxexx(nxt)
    if se:
        season, episode = se

    if season is not None:
        homelander_url = build_homelander_url("episodes", imdb, tmdb, title, year, season=season)
    else:
        homelander_url = build_homelander_url("seasons", imdb, tmdb, title, year)

    xbmc.log(f"[SIMKL Watching] open_homelander series URL: {homelander_url}", xbmc.LOGINFO)
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


def tmdb_poster_url(poster_path, size_suffix="w342"):
    """
    Build a TMDB image URL for movie posters.
    """
    if not poster_path:
        return None
    return f"https://image.tmdb.org/t/p/{size_suffix}{poster_path}"


# --------------------------
# TMDB airdate helpers

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
    add_folder("Movies", "movies", icon=f"{MEDIA_PATH}/movies.png")
    add_folder("Search", "search_menu", icon=f"{MEDIA_PATH}/search.png")
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

            se = parse_sxxexx(oldest)
            url = build_url(
                action="show_season_episodes",
                title=title,
                imdb=imdb_id,
                tmdb=tmdb_id,
                year=year,
                next=oldest if oldest != "unknown next episode" else "",
                season_num=se[0] if se else 1,
                simkl_poster=poster_path or ""
            )

            add_item(label, url=url, info={"title": title}, art=art, is_folder=True)

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
                return (1, float("inf"), title.lower())
            return (0, d.toordinal(), title.lower())

        rows.sort(key=_sort_key)

        show_posters = addon.getSettingBool("show_posters")

        for _, title, _, airdate, poster_path in rows:
            # Keep your existing label rules/countdown, just change ordering.
            d = _days_until(airdate)

            if d is None or d < 0:
                label = f"{title}"
            elif d < 3:
                target_d = _parse_ymd_date(airdate)
                end_of_day = datetime(target_d.year, target_d.month, target_d.day, 23, 59, 59)
                hours = int((end_of_day - datetime.now()).total_seconds() / 3600)
                label = f"{title} — {hours} hours — ({airdate})"
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


def show_movies():
    xbmcplugin.setPluginCategory(HANDLE, "Movies")
    addon = xbmcaddon.Addon()
    api = SimklApi(addon)

    if not api.is_authorized():
        add_item("Not authorized. Run 'Authorize SIMKL' first.")
        end_dir()
        return

    try:
        data = api.get_plan_movies()
        if addon.getSettingBool("debug_logging"):
            xbmc.log(f"[SIMKL Watching] /sync/all-items/movies/plan FULL response: {data}", xbmc.LOGINFO)

        movies = []
        if isinstance(data, dict):
            if isinstance(data.get("movies"), list):
                movies = data["movies"]
                if addon.getSettingBool("debug_logging"):
                    xbmc.log(f"[SIMKL Watching] Extracted {len(movies)} movies from 'movies' key", xbmc.LOGINFO)
            elif isinstance(data.get("items"), list):
                movies = data["items"]
                if addon.getSettingBool("debug_logging"):
                    xbmc.log(f"[SIMKL Watching] Extracted {len(movies)} movies from 'items' key", xbmc.LOGINFO)
            elif isinstance(data.get("data"), list):
                movies = data["data"]
                if addon.getSettingBool("debug_logging"):
                    xbmc.log(f"[SIMKL Watching] Extracted {len(movies)} movies from 'data' key", xbmc.LOGINFO)
        elif isinstance(data, list):
            movies = data
            if addon.getSettingBool("debug_logging"):
                xbmc.log(f"[SIMKL Watching] Data is already a list with {len(movies)} items", xbmc.LOGINFO)

        def _item_is_plan_movie(item):
            if not isinstance(item, dict):
                return False
            status = item.get("status") or item.get("list")
            if not status:
                movie = item.get("movie") or item.get("film") or {}
                status = movie.get("status") or movie.get("list")
            if not status:
                return False
            return str(status).strip().lower() in (
                "plan",
                "plan to watch",
                "planned",
                "plan_to_watch",
                "plantowatch",
                "watchlist",
            )

        movies = [item for item in movies if _item_is_plan_movie(item)]
        
        if addon.getSettingBool("debug_logging"):
            xbmc.log(f"[SIMKL Watching] After status filter: {len(movies)} movies remaining", xbmc.LOGINFO)
            if movies:
                xbmc.log(f"[SIMKL Watching] First movie for debug: {movies[0]}", xbmc.LOGINFO)

        def _movie_like(item):
            if not isinstance(item, dict):
                return False
            if item.get("movie") or item.get("film"):
                return True
            if item.get("title") or item.get("year") or item.get("ids"):
                return True
            return False

        if not movies and isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, list) and any(_movie_like(x) for x in value):
                    movies = value
                    if addon.getSettingBool("debug_logging"):
                        xbmc.log(f"[SIMKL Watching] fallback movie list from key={key}", xbmc.LOGINFO)
                    break

        if not movies:
            add_item("No movies found in your SIMKL Plan to Watch list.")
            end_dir()
            return

        def _extract_movie_object(item):
            if not isinstance(item, dict):
                return {}
            movie = item.get("movie") or item.get("film") or item
            if not isinstance(movie, dict):
                return {}
            return movie

        movies = sorted(
            movies,
            key=lambda item: (_extract_movie_object(item).get("title", "").lower())
        )
        tmdb_enabled = use_tmdb_airdates(addon)
        show_posters = addon.getSettingBool("show_posters")

        for it in movies:
            movie = _extract_movie_object(it)
            ids = movie.get("ids") or {}

            title = movie.get("title") or movie.get("name") or "Unknown title"
            year = movie.get("year", "")
            imdb_id = ids.get("imdb", "")
            tmdb_id = ids.get("tmdb", "")
            poster_path = movie.get("poster") or movie.get("poster_path")
            overview = movie.get("overview") or movie.get("plot") or ""
            release_date = movie.get("released") or movie.get("release_date") or ""

            tmdb_art = None
            if tmdb_enabled and tmdb_id:
                try:
                    details = TmdbApi(addon).movie_details(int(tmdb_id))
                    if not overview:
                        overview = details.get("overview") or overview
                    if not release_date:
                        release_date = details.get("release_date") or release_date
                    tmdb_art = tmdb_poster_url(details.get("poster_path"))
                except Exception as e:
                    xbmc.log(f"[SIMKL Watching][TMDB] movie details lookup failed: {e}", xbmc.LOGERROR)

            label = title
            if year:
                label = f"{title} ({year})"
            if release_date and release_date != year:
                label = f"{label} — {release_date}"

            art = None
            if show_posters:
                if tmdb_art:
                    art = {"thumb": tmdb_art, "poster": tmdb_art, "icon": tmdb_art}
                else:
                    url = simkl_poster_url(poster_path)
                    if url:
                        art = {"thumb": url, "poster": url, "icon": url}

            info = {"title": title}
            if year:
                info["year"] = str(year)
            if overview:
                info["plot"] = overview

            url = build_url(action="open_homelander", title=title, imdb=imdb_id, tmdb=tmdb_id, year=year, media_type="movie")
            
            add_item(label, url=url, info=info, art=art, is_folder=False)

        end_dir()

    except Exception as e:
        xbmc.log(f"[SIMKL Watching] Movies failed: {e}", xbmc.LOGERROR)
        add_item("Failed to fetch SIMKL data. Check kodi.log.")
        end_dir()


def show_search_menu():
    xbmcplugin.setPluginCategory(HANDLE, "Search")
    add_item("Series", url=build_url(action="search_series"), art={"icon": f"{MEDIA_PATH}/new.png", "thumb": f"{MEDIA_PATH}/new.png"})
    add_item("Movies", url=build_url(action="search_movies"), art={"icon": f"{MEDIA_PATH}/movies.png", "thumb": f"{MEDIA_PATH}/movies.png"})
    end_dir()


def _homelander_search(prompt, homelander_url_fn):
    term = xbmcgui.Dialog().input(prompt, type=xbmcgui.INPUT_ALPHANUM)
    if not term or not term.strip():
        return
    homelander_url = homelander_url_fn(term.strip())
    xbmc.sleep(500)
    xbmc.executebuiltin(f'ActivateWindow(10025,"{homelander_url}",return)')


def search_series():
    addon = xbmcaddon.Addon()
    tmdb = TmdbApi(addon)

    if not tmdb.is_configured():
        xbmcgui.Dialog().notification(
            "Search Series",
            "TMDB API key required. Add it in Settings.",
            xbmcgui.NOTIFICATION_WARNING,
        )
        return

    term = xbmcgui.Dialog().input("Search Series", type=xbmcgui.INPUT_ALPHANUM)
    if not term or not term.strip():
        return

    try:
        results = tmdb.search_tv(term.strip())
    except Exception as e:
        xbmc.log(f"[SIMKL Watching] TMDB search_tv failed: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("Search Series", "TMDB search failed", xbmcgui.NOTIFICATION_ERROR)
        return

    items = results.get("results") or []
    if not items:
        xbmcgui.Dialog().notification("Search Series", "No results found", xbmcgui.NOTIFICATION_INFO)
        return

    # Build selection list (cap at 10 for usability)
    items = items[:10]
    labels = []
    for r in items:
        name = r.get("name") or r.get("original_name") or "Unknown"
        first_air = (r.get("first_air_date") or "")[:4]
        labels.append(f"{name} ({first_air})" if first_air else name)

    choice = xbmcgui.Dialog().select("Select Series", labels)
    if choice < 0:
        return

    chosen = items[choice]
    tmdb_id = chosen.get("id")
    title = chosen.get("name") or chosen.get("original_name") or term.strip()
    year = (chosen.get("first_air_date") or "")[:4]

    try:
        ext = tmdb.tv_external_ids(int(tmdb_id))
    except Exception as e:
        xbmc.log(f"[SIMKL Watching] TMDB tv_external_ids failed: {e}", xbmc.LOGERROR)
        ext = {}

    imdb_id = ext.get("imdb_id") or ""
    tvdb_id = ext.get("tvdb_id") or ""

    homelander_url = build_homelander_url("seasons", imdb_id, str(tmdb_id), title, year)
    xbmc.sleep(500)
    xbmc.executebuiltin(f'ActivateWindow(10025,"{homelander_url}",return)')


def search_movies():
    _homelander_search(
        "Search Movies",
        lambda term: (
            "plugin://plugin.video.homelander/?action=movieSearchterm&name="
            + urllib.parse.quote(term)
        ),
    )


def show_help():
    xbmcplugin.setPluginCategory(HANDLE, "Settings / Help")
    add_folder("Authorize SIMKL", "auth", icon=f"{MEDIA_PATH}/auth.png")
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

    token = None
    cancelled = False
    try:
        token = api.poll_pin(user_code, interval, expires_in, progress_cb=progress_cb, cancel_fn=dp.iscanceled)
        cancelled = dp.iscanceled()
    finally:
        dp.close()

    if token:
        api.save_token(token)
        xbmcgui.Dialog().notification("SIMKL", "Authorized successfully!", xbmcgui.NOTIFICATION_INFO)
    elif cancelled:
        xbmcgui.Dialog().notification("SIMKL", "Authorization cancelled", xbmcgui.NOTIFICATION_WARNING)
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
# Season browser
# --------------------------
def show_seasons(params):
    title = params.get("title", "")
    imdb_id = params.get("imdb", "")
    tmdb_id = params.get("tmdb", "")
    year = params.get("year", "")
    next_ep = params.get("next", "")
    simkl_poster = params.get("simkl_poster", "")

    se = parse_sxxexx(next_ep)
    current_season = se[0] if se else 1

    xbmcplugin.setPluginCategory(HANDLE, f"{title} — Seasons")
    addon = xbmcaddon.Addon()

    seasons = []  # list of (season_num, name, ep_count, poster_path)
    show_poster_url = simkl_poster_url(simkl_poster) if simkl_poster else None

    if tmdb_id:
        try:
            tmdb = TmdbApi(addon)
            if tmdb.is_configured():
                tv = tmdb.tv_details(int(tmdb_id))
                for s in tv.get("seasons", []) or []:
                    sn = s.get("season_number", 0)
                    if sn == 0:
                        continue
                    seasons.append((
                        sn,
                        s.get("name") or f"Season {sn}",
                        s.get("episode_count") or 0,
                        s.get("poster_path"),
                    ))
        except Exception as e:
            xbmc.log(f"[SIMKL Watching] TMDB tv_details failed: {e}", xbmc.LOGERROR)

    if not seasons:
        seasons = [(n, f"Season {n}", 0, None) for n in range(1, current_season + 1)]

    for sn, name, ep_count, poster_path in seasons:
        label = f"► {name}" if sn == current_season else name
        if ep_count:
            label += f"  ({ep_count} eps)"

        if poster_path:
            purl = tmdb_poster_url(poster_path)
            art = {"thumb": purl, "poster": purl, "icon": purl}
        elif show_poster_url:
            art = {"thumb": show_poster_url, "poster": show_poster_url, "icon": show_poster_url}
        else:
            art = None

        url = build_url(
            action="show_season_episodes",
            title=title, imdb=imdb_id, tmdb=tmdb_id, year=year,
            next=next_ep, season_num=sn,
            simkl_poster=simkl_poster
        )
        add_item(label, url=url, art=art, is_folder=True)

    end_dir()


# --------------------------
# Season episode list
# --------------------------
def show_season_episodes(params):
    title = params.get("title", "")
    imdb_id = params.get("imdb", "")
    tmdb_id = params.get("tmdb", "")
    year = params.get("year", "")
    next_ep = params.get("next", "")
    simkl_poster = params.get("simkl_poster", "")

    # season_num overrides the season from next_ep (used when browsing other seasons)
    se = parse_sxxexx(next_ep)
    next_ep_num = se[1] if se else None
    next_season = se[0] if se else 1

    try:
        season = int(params["season_num"])
    except (KeyError, ValueError, TypeError):
        season = next_season

    if not season:
        open_homelander(params)
        return

    xbmcplugin.setPluginCategory(HANDLE, f"{title} — Season {season}")
    addon = xbmcaddon.Addon()

    episodes = []  # list of (ep_num, ep_title, air_date, still_path)
    show_poster_url = simkl_poster_url(simkl_poster) if simkl_poster else None

    if tmdb_id:
        try:
            tmdb = TmdbApi(addon)
            if tmdb.is_configured():
                season_data = tmdb.tv_season(int(tmdb_id), season)
                for ep in season_data.get("episodes", []) or []:
                    ep_num = ep.get("episode_number")
                    if ep_num:
                        episodes.append((
                            int(ep_num),
                            ep.get("name") or f"Episode {ep_num}",
                            ep.get("air_date") or "",
                            ep.get("still_path"),
                        ))
        except Exception as e:
            xbmc.log(f"[SIMKL Watching] TMDB season fetch failed: {e}", xbmc.LOGERROR)

    if not episodes:
        episodes = [(n, f"Episode {n}", "", None) for n in range(1, 27)]

    all_seasons_url = build_url(
        action="show_seasons",
        title=title, imdb=imdb_id, tmdb=tmdb_id, year=year,
        next=next_ep, simkl_poster=simkl_poster
    )
    art_fallback = {"thumb": show_poster_url, "icon": show_poster_url} if show_poster_url else None
    add_item("« All Seasons", url=all_seasons_url, art=art_fallback, is_folder=True)

    # Fetch AllDebrid availability for the whole season in one pass
    ad_available = set()
    try:
        ad_available = AllDebridApi().get_available_episodes(title, season)
    except Exception as e:
        xbmc.log(f"[SIMKL Watching] AllDebrid availability check failed: {e}", xbmc.LOGERROR)

    today = date.today()

    for ep_num, ep_title, air_date, still_path in episodes:
        code = f"S{season:02d}E{ep_num:02d}"

        label = f"{code} — {ep_title}"
        if air_date:
            label += f"  ({air_date})"
        if season == next_season and ep_num == next_ep_num:
            label = f"► {label}"
        if air_date:
            d = _parse_ymd_date(air_date)
            if d and d > today:
                label += "  [upcoming]"

        if still_path:
            surl = f"https://image.tmdb.org/t/p/w300{still_path}"
            art = {"thumb": surl, "icon": surl}
        elif show_poster_url:
            art = {"thumb": show_poster_url, "icon": show_poster_url}
        else:
            art = None

        hl_url = build_url(
            action="open_homelander",
            title=title, imdb=imdb_id, tmdb=tmdb_id, year=year,
            next=code
        )

        ctx = [(
            "Play via AllDebrid",
            f"RunPlugin({build_url(action='play_alldebrid', title=title, season=season, episode=ep_num)})"
        )]

        ad_label = "AD ✓" if ep_num in ad_available else ""

        add_item(
            label, url=hl_url,
            info={"title": ep_title, "episode": ep_num, "season": season},
            art=art, context_menu=ctx,
            label2=ad_label,
            is_folder=False
        )

    end_dir()


# --------------------------
# AllDebrid playback
# --------------------------
def play_alldebrid(params):
    title = params.get("title", "")
    try:
        season = int(params.get("season", 0))
        episode = int(params.get("episode", 0))
    except (ValueError, TypeError):
        return

    try:
        stream_url, fname = AllDebridApi().find_episode(title, season, episode)
    except Exception as e:
        xbmc.log(f"[SIMKL Watching][AllDebrid] find_episode error: {e}", xbmc.LOGERROR)
        stream_url, fname = None, None

    if not stream_url:
        xbmcgui.Dialog().notification(
            "AllDebrid",
            f"No match: {title} S{season:02d}E{episode:02d}",
            xbmcgui.NOTIFICATION_WARNING,
            3000,
        )
        return

    label = fname or f"{title} S{season:02d}E{episode:02d}"
    li = xbmcgui.ListItem(label=label, path=stream_url)
    li.setInfo("video", {"title": label})
    li.setProperty("IsPlayable", "true")
    xbmc.Player().play(stream_url, li)


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
    elif action == "movies":
        show_movies()
    elif action == "search_menu":
        show_search_menu()
    elif action == "search_series":
        search_series()
    elif action == "search_movies":
        search_movies()
    elif action == "help":
        show_help()
    elif action == "auth":
        show_auth()
    elif action == "show_seasons":
        show_seasons(params)
    elif action == "show_season_episodes":
        show_season_episodes(params)
    elif action == "open_homelander":
        open_homelander(params)
    elif action == "play_alldebrid":
        play_alldebrid(params)
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