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
from resources.lib.local_media import LocalMediaApi
from resources.lib.iptv_api import IptvApi
from resources.lib.iptv_http import UA as IPTV_UA
from resources.lib.ui import get_params, build_url, add_folder, add_item, end_dir
from resources.lib import livetv
from resources.lib import settings_reset
from resources.lib import legacy_import


ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo("path")
MEDIA_PATH = f"{ADDON_PATH}/resources/media"


HANDLE = int(sys.argv[1])

# --------------------------
# Logging / utility
# --------------------------
def log(msg):
    xbmc.log(f"[WhatsOnStreamer] {msg}", xbmc.LOGINFO)


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
def build_homelander_url(action, imdb, tmdb, tvshowtitle, year, season=None, episode=None):
    params = {
        "action": action,
        "imdb": imdb or "",
        "tmdb": tmdb or "",
        "tvshowtitle": tvshowtitle or "",
        "year": str(year or ""),
    }
    if season is not None:
        params["season"] = str(season)
    if episode is not None:
        params["episode"] = str(episode)
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"plugin://plugin.video.homelander/?{qs}"


def build_homelander_play_url(imdb, tmdb, tvshowtitle, year, season, episode, ep_title="", premiered=""):
    import json as _json
    params = {
        "action": "play",
        "title": ep_title or tvshowtitle or "",
        "year": str(year or ""),
        "imdb": imdb or "",
        "tmdb": tmdb or "",
        "season": str(season),
        "episode": str(episode),
        "tvshowtitle": tvshowtitle or "",
        "premiered": premiered or "",
        "meta": _json.dumps({}),
    }
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"plugin://plugin.video.homelander/?{qs}"


def open_homelander(params):
    title = params.get("title", "")
    imdb = params.get("imdb", "")
    tmdb = params.get("tmdb", "")
    year = params.get("year", "")
    nxt = params.get("next", "")  # e.g. "S01E03" (may be empty)
    ep_title = params.get("ep_title", "")
    air_date = params.get("air_date", "")
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

    if season is not None and episode is not None:
        homelander_url = build_homelander_play_url(imdb, tmdb, title, year, season, episode, ep_title, air_date)
        xbmc.log(f"[WhatsOnStreamer] open_homelander play URL: {homelander_url}", xbmc.LOGINFO)
        xbmc.executebuiltin(f'RunPlugin("{homelander_url}")')
    else:
        homelander_url = build_homelander_url("seasons", imdb, tmdb, title, year)
        xbmc.log(f"[WhatsOnStreamer] open_homelander seasons URL: {homelander_url}", xbmc.LOGINFO)
        xbmc.executebuiltin(f'ActivateWindow(10025,"{homelander_url}",return)')


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
        xbmc.log(f"[WhatsOnStreamer][TMDB] airdate lookup failed: {e}", xbmc.LOGERROR)
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
        xbmc.log(f"[WhatsOnStreamer][TMDB] next_episode lookup failed: {e}", xbmc.LOGERROR)
        return None


# --------------------------
# Screens
# --------------------------
def show_root_menu():
    xbmcplugin.setPluginCategory(HANDLE, "WhatsOnStreamer")
    add_folder("WhatsOnNow (Live TV)", "livetv_root", icon=f"{MEDIA_PATH}/on_now.png")
    add_folder("WhatsUpNext (SIMKL)", "whatsupnext_root", icon=f"{MEDIA_PATH}/new.png")
    add_folder("Tools", "tools_root", icon=f"{MEDIA_PATH}/tools_diagnostics.png")
    end_dir()


def show_whatsupnext_menu():
    xbmcplugin.setPluginCategory(HANDLE, "WhatsUpNext")
    add_folder("New Episodes", "new", icon=f"{MEDIA_PATH}/new.png")
    add_folder("Upcoming Episodes", "upcoming", icon=f"{MEDIA_PATH}/upcoming.png")
    add_folder("Movies", "movies", icon=f"{MEDIA_PATH}/movies.png")
    add_folder("AllDebrid", "alldebrid_menu", icon=f"{MEDIA_PATH}/alldebrid.png")
    add_folder("Search", "search_menu", icon=f"{MEDIA_PATH}/search.png")
    add_folder("Settings / Help", "help", icon=f"{MEDIA_PATH}/help.png")
    end_dir()


def show_tools_menu():
    xbmcplugin.setPluginCategory(HANDLE, "Tools")
    add_folder("Authorize SIMKL", "auth", icon=f"{MEDIA_PATH}/auth.png")
    add_item("Live TV: Build info", url=build_url(action="livetv_build_info"))
    add_item("Live TV: Run auto-update now", url=build_url(action="svc_update"))
    add_item("Live TV: Test connection", url=build_url(action="livetv_test"))
    add_item("Live TV: Test local files", url=build_url(action="livetv_test_local"))
    add_item("Import settings from WhatsOnNow / WhatsUpNext", url=build_url(action="settings_import"))
    add_item("Reset settings to defaults", url=build_url(action="settings_reset"))
    add_item("Open Settings", url=build_url(action="settings"))
    end_dir()


def show_new_episodes():
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
            xbmc.log(f"[WhatsOnStreamer] /sync/all-items/shows/watching response: {data}", xbmc.LOGINFO)

        shows = data.get("shows", []) if isinstance(data, dict) else []
        if not shows:
            add_item("No items returned from WhatsOnStreamer list.")
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
            simkl_id = ids.get("simkl", "")
            poster_path = show.get("poster")
            overview = show.get("overview") or ""

            # Ratings from extended=full response
            ratings = show.get("ratings") or {}
            simkl_r = ratings.get("simkl") or {}
            simkl_rating = simkl_r.get("rating") or show.get("rating") or 0.0
            simkl_votes = simkl_r.get("votes") or 0

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

            rows.append((last_dt, new_count, title, oldest, airdate, poster_path, imdb_id, tmdb_id, year, simkl_id, overview, simkl_rating, simkl_votes))

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
        tmdb_api = TmdbApi(addon)

        for last_dt, new_count, title, oldest, airdate, poster_path, imdb_id, tmdb_id, year, simkl_id, overview, simkl_rating, simkl_votes in rows:
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
                simkl_poster=poster_path or "",
                simkl_id=simkl_id,
            )

            # TMDB details — uses cache (2-day TTL), fetches only on first encounter
            tmdb_vote_avg = None
            if tmdb_id and tmdb_api.is_configured():
                try:
                    tmdb_det = tmdb_api.tv_details(int(tmdb_id))
                    tmdb_vote_avg = tmdb_det.get("vote_average")
                    if not overview:
                        overview = tmdb_det.get("overview") or ""
                except Exception as e:
                    xbmc.log(f"[WhatsOnStreamer] TMDB tv_details failed for {title}: {e}", xbmc.LOGERROR)

            # SIMKL show-details cache — last-resort for shows with no TMDB id
            if not overview and simkl_id:
                simkl_cached = api.get_show_details_if_cached(int(simkl_id))
                if simkl_cached:
                    overview = simkl_cached.get("overview") or ""
                    if not simkl_rating:
                        r = (simkl_cached.get("ratings") or {}).get("simkl") or {}
                        simkl_rating = r.get("rating") or simkl_cached.get("rating") or 0.0
                        simkl_votes = r.get("votes") or 0

            # Build plot: overview + ratings footer
            rating_parts = []
            if simkl_rating:
                votes_str = f"  ({simkl_votes:,})" if simkl_votes else ""
                rating_parts.append(f"SIMKL  {simkl_rating:.1f}/10{votes_str}")
            if tmdb_vote_avg:
                rating_parts.append(f"TMDB  {tmdb_vote_avg:.1f}/10")
            plot = overview
            if rating_parts:
                plot = (plot + "\n\n" if plot else "") + "  ·  ".join(rating_parts)

            info = {"title": title, "tvshowtitle": title}
            if plot:
                info["plot"] = plot
            if simkl_rating:
                info["rating"] = float(simkl_rating)
            if simkl_votes:
                info["votes"] = str(simkl_votes)

            ctx = [(
                "Watch Trailer",
                f"RunPlugin({build_url(action='play_trailer', title=title, simkl_id=simkl_id)})",
            )]
            add_item(label, url=url, info=info, art=art, is_folder=True, context_menu=ctx)

        end_dir()

    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer] New Episodes failed: {e}", xbmc.LOGERROR)
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
            xbmc.log(f"[WhatsOnStreamer] /sync/all-items/shows/watching response: {data}", xbmc.LOGINFO)

        shows = data.get("shows", []) if isinstance(data, dict) else []
        if not shows:
            add_item("No items returned from WhatsOnStreamer list.")
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
        xbmc.log(f"[WhatsOnStreamer] Upcoming failed: {e}", xbmc.LOGERROR)
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
            xbmc.log(f"[WhatsOnStreamer] /sync/all-items/movies/plan FULL response: {data}", xbmc.LOGINFO)

        movies = []
        if isinstance(data, dict):
            if isinstance(data.get("movies"), list):
                movies = data["movies"]
                if addon.getSettingBool("debug_logging"):
                    xbmc.log(f"[WhatsOnStreamer] Extracted {len(movies)} movies from 'movies' key", xbmc.LOGINFO)
            elif isinstance(data.get("items"), list):
                movies = data["items"]
                if addon.getSettingBool("debug_logging"):
                    xbmc.log(f"[WhatsOnStreamer] Extracted {len(movies)} movies from 'items' key", xbmc.LOGINFO)
            elif isinstance(data.get("data"), list):
                movies = data["data"]
                if addon.getSettingBool("debug_logging"):
                    xbmc.log(f"[WhatsOnStreamer] Extracted {len(movies)} movies from 'data' key", xbmc.LOGINFO)
        elif isinstance(data, list):
            movies = data
            if addon.getSettingBool("debug_logging"):
                xbmc.log(f"[WhatsOnStreamer] Data is already a list with {len(movies)} items", xbmc.LOGINFO)

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
            xbmc.log(f"[WhatsOnStreamer] After status filter: {len(movies)} movies remaining", xbmc.LOGINFO)
            if movies:
                xbmc.log(f"[WhatsOnStreamer] First movie for debug: {movies[0]}", xbmc.LOGINFO)

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
                        xbmc.log(f"[WhatsOnStreamer] fallback movie list from key={key}", xbmc.LOGINFO)
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
                    xbmc.log(f"[WhatsOnStreamer][TMDB] movie details lookup failed: {e}", xbmc.LOGERROR)

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
        xbmc.log(f"[WhatsOnStreamer] Movies failed: {e}", xbmc.LOGERROR)
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
        xbmc.log(f"[WhatsOnStreamer] TMDB search_tv failed: {e}", xbmc.LOGERROR)
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
        xbmc.log(f"[WhatsOnStreamer] TMDB tv_external_ids failed: {e}", xbmc.LOGERROR)
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
    add_item("SIMKL authorization moved to the root Tools menu.")
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
        xbmc.log(f"[WhatsOnStreamer] PIN request failed: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("SIMKL", "PIN request failed", xbmcgui.NOTIFICATION_ERROR)
        show_whatsupnext_menu()
        return

    user_code = pin.get("user_code")
    verify_url = pin.get("verification_url") or "https://simkl.com/pin/"
    expires_in = int(pin.get("expires_in", 900))
    interval = int(pin.get("interval", 5))

    if not user_code:
        xbmcgui.Dialog().notification("SIMKL", "PIN response missing user_code", xbmcgui.NOTIFICATION_ERROR)
        show_whatsupnext_menu()
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

    show_whatsupnext_menu()


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
        xbmc.log(f"[WhatsOnStreamer] show details failed: {e}", xbmc.LOGERROR)
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
    simkl_id = params.get("simkl_id", "")

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
            xbmc.log(f"[WhatsOnStreamer] TMDB tv_details failed: {e}", xbmc.LOGERROR)

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
            simkl_poster=simkl_poster, simkl_id=simkl_id,
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
    simkl_id = params.get("simkl_id", "")

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
    xbmcplugin.setContent(HANDLE, "episodes")
    addon = xbmcaddon.Addon()

    episodes = []  # list of dicts; see TMDB extraction block below
    show_poster_url = simkl_poster_url(simkl_poster) if simkl_poster else None

    if tmdb_id:
        try:
            tmdb = TmdbApi(addon)
            if tmdb.is_configured():
                season_data = tmdb.tv_season(int(tmdb_id), season)
                for ep in season_data.get("episodes", []) or []:
                    ep_num = ep.get("episode_number")
                    if not ep_num:
                        continue
                    crew = ep.get("crew") or []
                    guest_stars = ep.get("guest_stars") or []
                    episodes.append({
                        "ep_num": int(ep_num),
                        "ep_title": ep.get("name") or f"Episode {ep_num}",
                        "air_date": ep.get("air_date") or "",
                        "still_path": ep.get("still_path"),
                        "overview": ep.get("overview") or "",
                        "vote_average": ep.get("vote_average") or 0.0,
                        "vote_count": ep.get("vote_count") or 0,
                        "runtime": ep.get("runtime"),
                        "directors": [c["name"] for c in crew if c.get("job") == "Director"],
                        "writers": [c["name"] for c in crew if c.get("job") in ("Writer", "Screenplay", "Story", "Teleplay")],
                        "cast": [
                            {
                                "name": g["name"],
                                "role": g.get("character") or "",
                                "thumbnail": f"https://image.tmdb.org/t/p/w185{g['profile_path']}" if g.get("profile_path") else "",
                            }
                            for g in guest_stars
                        ],
                    })
        except Exception as e:
            xbmc.log(f"[WhatsOnStreamer] TMDB season fetch failed: {e}", xbmc.LOGERROR)

    if not episodes:
        episodes = [
            {"ep_num": n, "ep_title": f"Episode {n}", "air_date": "", "still_path": None,
             "overview": "", "vote_average": 0.0, "vote_count": 0, "runtime": None,
             "directors": [], "writers": [], "cast": []}
            for n in range(1, 27)
        ]

    all_seasons_url = build_url(
        action="show_seasons",
        title=title, imdb=imdb_id, tmdb=tmdb_id, year=year,
        next=next_ep, simkl_poster=simkl_poster, simkl_id=simkl_id,
    )
    art_fallback = {"thumb": show_poster_url, "icon": show_poster_url} if show_poster_url else None
    add_item("« All Seasons", url=all_seasons_url, art=art_fallback, is_folder=True)

    # Fetch AllDebrid availability for the whole season in one pass
    ad_available = set()
    try:
        ad_api = AllDebridApi()
        if ad_api.is_configured():
            ad_available = ad_api.get_available_episodes(title, season)
    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer] AllDebrid availability check failed: {e}", xbmc.LOGERROR)

    # Fetch local file availability — dict of {ep_num: file_path}
    lf_available = {}
    try:
        lf_api = LocalMediaApi()
        if lf_api.is_configured():
            lf_available = lf_api.get_available_episodes_with_paths(title, season)
    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer] Local media availability check failed: {e}", xbmc.LOGERROR)

    # Fetch IPTV (Xtream series) availability for the whole season in one pass
    iptv_available = set()
    try:
        iptv_api = IptvApi()
        if iptv_api.is_configured():
            iptv_available = iptv_api.get_available_episodes(title, season)
    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer] IPTV availability check failed: {e}", xbmc.LOGERROR)

    today = date.today()

    for ep in episodes:
        ep_num    = ep["ep_num"]
        ep_title  = ep["ep_title"]
        air_date  = ep["air_date"]
        still_path = ep["still_path"]
        overview   = ep["overview"]
        vote_average = ep["vote_average"]
        vote_count   = ep["vote_count"]
        runtime      = ep["runtime"]
        directors    = ep["directors"]
        writers      = ep["writers"]
        ep_cast      = ep["cast"]

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
            next=code, ep_title=ep_title, air_date=air_date
        )

        # Default click priority: local file -> IPTV -> Homelander (catch-all).
        # AllDebrid stays "Play via AllDebrid"-only: once Homelander is handed off
        # it can't report failure back to us, so nothing can fall through past it.
        local_path = lf_available.get(ep_num)
        if local_path:
            default_url = local_path
            is_playable = True
        elif ep_num in iptv_available:
            default_url = build_url(action="play_iptv", title=title, season=season, episode=ep_num)
            is_playable = False
        else:
            default_url = hl_url
            is_playable = False

        if ep_num in ad_available:
            label += "  [COLOR lime]● AD[/COLOR]"
        if local_path:
            label += "  [COLOR dodgerblue]● LF[/COLOR]"
        if ep_num in iptv_available:
            label += "  [COLOR orange]● IPTV[/COLOR]"

        ctx = [
            ("Play via AllDebrid", f"RunPlugin({build_url(action='play_alldebrid', title=title, season=season, episode=ep_num)})"),
        ]
        if local_path:
            ctx.append(("Play local file", f"RunPlugin({build_url(action='play_local', title=title, season=season, episode=ep_num)})"))
        ctx.append(("Play via Homelander", f"RunPlugin({hl_url})"))
        ctx.append(("Play via IPTV", f"RunPlugin({build_url(action='play_iptv', title=title, season=season, episode=ep_num)})"))
        if simkl_id:
            ctx.append(("Watch Trailer", f"RunPlugin({build_url(action='play_trailer', title=title, simkl_id=simkl_id)})"))

        info = {
            "title": ep_title,
            "tvshowtitle": title,
            "episode": ep_num,
            "season": season,
        }
        if overview:
            info["plot"] = overview
        if vote_average:
            info["rating"] = float(vote_average)
        if vote_count:
            info["votes"] = str(vote_count)
        if air_date:
            info["premiered"] = air_date
        if runtime:
            info["duration"] = int(runtime) * 60
        if directors:
            info["director"] = ", ".join(directors)
        if writers:
            info["writer"] = ", ".join(writers)

        add_item(
            label, url=default_url,
            info=info,
            art=art, context_menu=ctx,
            is_folder=False,
            cast=ep_cast or None,
            is_playable=is_playable,
        )

    end_dir()


_AD_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts"}


def _ad_is_video(filename):
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return ext in _AD_VIDEO_EXTS


# --------------------------
# AllDebrid browser
# --------------------------
def show_alldebrid_menu():
    xbmcplugin.setPluginCategory(HANDLE, "AllDebrid")
    ad = AllDebridApi()
    if not ad.is_configured():
        add_item("AllDebrid API key not set — configure in Settings.")
        end_dir()
        return
    magnets = ad._get_all_magnets()
    add_folder("[COLOR yellow]In Progress[/COLOR]", "alldebrid_inprogress")
    add_folder("[COLOR yellow]Errors[/COLOR]", "alldebrid_errors")
    ready = [m for m in magnets if m.get("statusCode", -1) == 4]
    for m in ready:
        label = m.get("filename", f"Magnet {m.get('id', '?')}")
        size_str = _fmt_bytes(m.get("size", 0))
        url = build_url(action="alldebrid_magnet_files", magnet_id=str(m["id"]), label=label)
        li = xbmcgui.ListItem(label=label, label2=size_str)
        li.setArt({"icon": f"{MEDIA_PATH}/magnet.png", "thumb": f"{MEDIA_PATH}/magnet.png"})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=True)
    if not ready:
        add_item("No ready magnets.")
    end_dir()


def _fmt_bytes(n):
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def show_alldebrid_inprogress():
    xbmcplugin.setPluginCategory(HANDLE, "AllDebrid / In Progress")
    ad = AllDebridApi()
    if not ad.is_configured():
        add_item("AllDebrid API key not set — configure in Settings.")
        end_dir()
        return
    in_progress = [m for m in ad._get_all_magnets() if m.get("statusCode", -1) in (0, 1, 2, 3)]
    if not in_progress:
        add_item("No magnets in progress.")
        end_dir()
        return
    for m in in_progress:
        label = m.get("filename", f"Magnet {m.get('id', '?')}")
        size = m.get("size", 0)
        downloaded = m.get("downloaded", 0)
        speed = m.get("speed", 0)
        pct = m.get("completionPercent") or (round(downloaded / size * 100, 1) if size else 0)
        parts = [_fmt_bytes(size)]
        if pct:
            parts.append(f"{pct}%")
        if speed:
            parts.append(f"{_fmt_bytes(speed)}/s")
        detail = " | ".join(parts)
        li = xbmcgui.ListItem(label=label, label2=detail)
        xbmcplugin.addDirectoryItem(HANDLE, build_url(action="alldebrid_inprogress"), li, isFolder=False)
    end_dir()


def show_alldebrid_errors():
    xbmcplugin.setPluginCategory(HANDLE, "AllDebrid / Errors")
    ad = AllDebridApi()
    if not ad.is_configured():
        add_item("AllDebrid API key not set — configure in Settings.")
        end_dir()
        return
    errors = [m for m in ad._get_all_magnets() if m.get("statusCode", -1) >= 5]
    if not errors:
        add_item("No errored magnets.")
        end_dir()
        return
    for m in errors:
        label = m.get("filename", f"Magnet {m.get('id', '?')}")
        add_item(label, label2=m.get("error") or m.get("status") or "Error")
    end_dir()


def show_alldebrid_magnet_files(params):
    magnet_id = params.get("magnet_id", "")
    parent_label = params.get("label", f"Magnet {magnet_id}")
    xbmcplugin.setPluginCategory(HANDLE, f"AllDebrid / {parent_label}")
    ad = AllDebridApi()
    try:
        files = ad._get_magnet_files(int(magnet_id))
    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer][AllDebrid] magnet files error: {e}", xbmc.LOGERROR)
        add_item("Failed to load files.")
        end_dir()
        return
    video_files = [(fname, link, size) for fname, link, size in files if _ad_is_video(fname)]
    if not video_files:
        add_item("No video files in this magnet.")
        end_dir()
        return
    for fname, link, size in video_files:
        display = fname.rsplit("/", 1)[-1]
        size_str = _fmt_bytes(size) if size else ""
        url = build_url(action="play_alldebrid_link", link=link, title=display)
        li = xbmcgui.ListItem(label=display, label2=size_str)
        li.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(HANDLE, url, li, isFolder=False)
    end_dir()


_AD_MIME = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".wmv": "video/x-ms-wmv",
    ".ts":  "video/mp2t",
    ".m2ts": "video/mp2t",
}


def _ad_resolve_and_play(stream_url, title, use_resolved_url=False, user_agent=None):
    """Shared playback helper for AllDebrid streams and direct IPTV streams.

    user_agent is only passed for direct IPTV provider URLs — those servers can
    silently reject Kodi's default player UA (empty body, instant EOF from the
    demuxer). AllDebrid's already-resolved CDN links don't need it and shouldn't
    get an unrelated header appended.
    """
    ext = ("." + title.rsplit(".", 1)[-1].lower()) if "." in title else ""
    mime = _AD_MIME.get(ext, "")
    play_path = f"{stream_url}|User-Agent={urllib.parse.quote(user_agent)}" if user_agent else stream_url
    li = xbmcgui.ListItem(label=title, path=play_path)
    li.setInfo("video", {"title": title})
    li.setContentLookup(False)
    if mime:
        li.setMimeType(mime)
    if use_resolved_url:
        xbmcplugin.setResolvedUrl(HANDLE, True, li)
    else:
        xbmc.Player().play(play_path, li)


def play_alldebrid_link(params):
    link = params.get("link", "")
    title = params.get("title", "")
    if not link:
        xbmcgui.Dialog().notification("AllDebrid", "No link provided", xbmcgui.NOTIFICATION_ERROR, 3000)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    ad = AllDebridApi()
    if not ad.is_configured():
        xbmcgui.Dialog().notification("AllDebrid", "API key not set — add it in Settings", xbmcgui.NOTIFICATION_WARNING, 3000)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    try:
        stream_url = ad.unlock_link(link)
    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer][AllDebrid] unlock error: {e}", xbmc.LOGERROR)
        stream_url = None
    if not stream_url:
        xbmcgui.Dialog().notification("AllDebrid", f"Failed to unlock: {title}", xbmcgui.NOTIFICATION_ERROR, 3000)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    _ad_resolve_and_play(stream_url, title, use_resolved_url=True)


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
        ad_api = AllDebridApi()
        if not ad_api.is_configured():
            xbmcgui.Dialog().notification("AllDebrid", "API key not set — add it in Settings", xbmcgui.NOTIFICATION_WARNING, 3000)
            return
        stream_url, fname = ad_api.find_episode(title, season, episode)
    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer][AllDebrid] find_episode error: {e}", xbmc.LOGERROR)
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
    _ad_resolve_and_play(stream_url, label)


# --------------------------
# IPTV (Xtream series) playback
# --------------------------
def play_iptv(params):
    title = params.get("title", "")
    try:
        season = int(params.get("season", 0))
        episode = int(params.get("episode", 0))
    except (ValueError, TypeError):
        return

    try:
        iptv_api = IptvApi()
        if not iptv_api.is_configured():
            xbmcgui.Dialog().notification("IPTV", "Provider not set — add it in Settings", xbmcgui.NOTIFICATION_WARNING, 3000)
            return
        stream_url, display = iptv_api.find_episode(title, season, episode)
    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer][IPTV] find_episode error: {e}", xbmc.LOGERROR)
        stream_url, display = None, None

    if not stream_url:
        xbmcgui.Dialog().notification(
            "IPTV",
            f"No match: {title} S{season:02d}E{episode:02d}",
            xbmcgui.NOTIFICATION_WARNING,
            3000,
        )
        return

    label = display or f"{title} S{season:02d}E{episode:02d}"
    _ad_resolve_and_play(stream_url, label, user_agent=IPTV_UA)


# --------------------------
# Local file playback
# --------------------------
def play_local(params):
    title = params.get("title", "")
    try:
        season = int(params.get("season", 0))
        episode = int(params.get("episode", 0))
    except (ValueError, TypeError):
        return

    lf_api = LocalMediaApi()
    if not lf_api.is_configured():
        xbmcgui.Dialog().notification("Local Media", "Base path not set — add it in Settings", xbmcgui.NOTIFICATION_WARNING, 3000)
        return

    file_path = lf_api.find_episode(title, season, episode)
    if not file_path:
        xbmcgui.Dialog().notification(
            "Local Media",
            f"No file found: {title} S{season:02d}E{episode:02d}",
            xbmcgui.NOTIFICATION_WARNING,
            3000,
        )
        return

    label = f"{title} S{season:02d}E{episode:02d}"
    li = xbmcgui.ListItem(label=label, path=file_path)
    li.setInfo("video", {"title": label})
    li.setProperty("IsPlayable", "true")
    xbmc.Player().play(file_path, li)


# --------------------------
# Trailer playback
# --------------------------
def play_trailer(params):
    title     = params.get("title", "")
    simkl_id  = params.get("simkl_id", "")
    addon     = xbmcaddon.Addon()
    youtube_id = None

    if simkl_id:
        try:
            api = SimklApi(addon)
            details = api.get_show_details_full(int(simkl_id))
            # SIMKL extended=full returns trailer as a bare YouTube video ID string
            youtube_id = details.get("trailer") or details.get("youtube")
            if not youtube_id:
                for t in (details.get("trailers") or []):
                    youtube_id = t.get("youtube") or t.get("id") if isinstance(t, dict) else None
                    if youtube_id:
                        break
        except Exception as e:
            xbmc.log(f"[WhatsOnStreamer] Trailer lookup failed: {e}", xbmc.LOGERROR)

    if youtube_id:
        xbmc.log(f"[WhatsOnStreamer] Playing trailer youtube_id={youtube_id} for {title}", xbmc.LOGINFO)
        xbmc.executebuiltin(f'RunPlugin("plugin://plugin.video.youtube/play/?video_id={youtube_id}")')
    else:
        xbmc.log(f"[WhatsOnStreamer] No SIMKL trailer for {title!r}, falling back to YouTube search", xbmc.LOGINFO)
        q = urllib.parse.quote(f"{title} official trailer")
        xbmc.executebuiltin(f'ActivateWindow(10025,"plugin://plugin.video.youtube/kodion/search/query/?q={q}",return)')


# --------------------------
# Router
# --------------------------
def router():
    params = get_params()
    action = params.get("action", "root")
    log(f"Action: {action}")

    if action == "root":
        show_root_menu()
    elif action == "whatsupnext_root":
        show_whatsupnext_menu()
    elif action == "tools_root":
        show_tools_menu()
    elif action == "settings":
        ADDON.openSettings()
    elif action == "settings_import":
        legacy_import.run()
    elif action == "settings_reset":
        settings_reset.confirm_and_reset()
    elif action == "svc_update":
        xbmc.executebuiltin('RunScript(special://home/addons/plugin.video.whatsonstreamer/service.py,manual)')
    elif action == "livetv_root":
        livetv.list_root()
    elif action == "livetv_build_info":
        livetv.show_build_info()
    elif action == "livetv_test":
        livetv.test_connection()
    elif action == "livetv_test_local":
        livetv.test_local_files()
    elif action == "livetv_on_now":
        livetv.list_on_now()
    elif action == "livetv_coming_up":
        livetv.list_coming_up()
    elif action == "livetv_favs":
        livetv.list_favourites()
    elif action == "livetv_recent":
        livetv.list_recent()
    elif action == "livetv_recent_clear":
        livetv.clear_recent(); xbmc.executebuiltin('Container.Refresh')
    elif action == "livetv_fav_clear":
        livetv.clear_favourites(); xbmc.executebuiltin('Container.Refresh')
    elif action == "livetv_fav_add":
        livetv.fav_add_index(params.get('i', '')); xbmc.executebuiltin('Container.Refresh')
    elif action == "livetv_fav_add_url":
        livetv.fav_add_url(params.get('u', ''), params.get('n', '')); xbmc.executebuiltin('Container.Refresh')
    elif action == "livetv_fav_remove_url":
        livetv.fav_remove_url(params.get('u', '')); xbmc.executebuiltin('Container.Refresh')
    elif action == "livetv_groups":
        livetv.list_groups()
    elif action == "livetv_group":
        livetv.list_group(params.get('name', ''), int(params.get('start', '0') or '0'))
    elif action == "livetv_all":
        livetv.list_all_channels(int(params.get('start', '0') or '0'))
    elif action == "livetv_search":
        livetv.do_search()
    elif action == "livetv_search_results":
        livetv.list_search_results()
    elif action == "livetv_play":
        livetv.play_channel_by_index(params.get('i', ''))
    elif action == "livetv_play_url":
        livetv.play_channel_by_url(params.get('u', ''), params.get('n', ''))
    elif action == "livetv_play_m3u":
        livetv.play_m3u(params.get('u', ''), params.get('n', ''))
    elif action == "livetv_refresh":
        livetv.load_playlist_index(force=True); xbmc.executebuiltin('Container.Refresh')
    elif action == "livetv_refresh_epg":
        _idx = livetv.load_playlist_index(force=False)
        livetv.load_epg_now_next(force=True, playlist_index=_idx)
        xbmc.executebuiltin('Container.Refresh')
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
    elif action == "play_trailer":
        play_trailer(params)
    elif action == "open_homelander":
        open_homelander(params)
    elif action == "alldebrid_menu":
        show_alldebrid_menu()
    elif action == "alldebrid_inprogress":
        show_alldebrid_inprogress()
    elif action == "alldebrid_errors":
        show_alldebrid_errors()
    elif action == "alldebrid_magnet_files":
        show_alldebrid_magnet_files(params)
    elif action == "play_alldebrid_link":
        play_alldebrid_link(params)
    elif action == "play_alldebrid":
        play_alldebrid(params)
    elif action == "play_local":
        play_local(params)
    elif action == "play_iptv":
        play_iptv(params)
    elif action.startswith("show_"):
        show_show_info(action)
    else:
        xbmcgui.Dialog().notification(
            "WhatsOnStreamer",
            f"Unknown action: {action}",
            xbmcgui.NOTIFICATION_ERROR
        )
        show_root_menu()


if __name__ == "__main__":
    router()