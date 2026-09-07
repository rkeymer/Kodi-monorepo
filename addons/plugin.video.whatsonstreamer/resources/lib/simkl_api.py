import json
import time
import urllib.parse
import urllib.request
import xbmc

from resources.lib.cache import DiskCache

API_BASE = "https://api.simkl.com"

_cache_show = DiskCache("simkl_show", ttl=7 * 86400)  # 7 days — trailer/ids rarely change
_cache_movie = DiskCache("simkl_movie", ttl=7 * 86400)  # 7 days — mirrors _cache_show
_cache_watching = DiskCache("simkl_watching", ttl=120)  # 2 min — account-wide, hit on every season screen

class SimklApi:
    def __init__(self, addon):
        self.addon = addon
        self.client_id = addon.getSettingString("client_id").strip()
        self.token = addon.getSettingString("access_token").strip()
        self.debug = addon.getSettingBool("debug_logging")

    def save_token(self, token: str):
        self.addon.setSettingString("access_token", token)
        self.token = token

    def is_configured(self) -> bool:
        return bool(self.client_id)

    def is_authorized(self) -> bool:
        return bool(self.token)

    def _headers(self, auth=False):
        # SIMKL requires simkl-api-key header with your client_id. [1](https://forums.trakt.tv/t/next-episode-missing-from-watched-progress-on-api/88273)
        headers = {
            "Content-Type": "application/json",
            "simkl-api-key": self.client_id,
            "User-Agent": "kodi-simkl-watching/0.1.1",
        }
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path, params=None, auth=False):
        if not self.client_id:
            raise RuntimeError("Missing SIMKL Client ID in add-on settings.")
        if auth and not self.token:
            raise RuntimeError("Missing SIMKL access token. Authorize first.")

        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url, headers=self._headers(auth=auth))
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data

    def _post(self, path, body, auth=False):
        if not self.client_id:
            raise RuntimeError("Missing SIMKL Client ID in add-on settings.")
        if auth and not self.token:
            raise RuntimeError("Missing SIMKL access token. Authorize first.")

        url = API_BASE + path
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers=self._headers(auth=auth), method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    def get_show_details_full(self, simkl_id: int):
        key = str(simkl_id)
        cached = _cache_show.get(key)
        if cached is not None:
            return cached
        data = self._get(f"/tv/{simkl_id}", params={"extended": "full"}, auth=False)
        _cache_show.set(key, data)
        return data

    def get_show_details_if_cached(self, simkl_id: int):
        """Returns cached show details without a network call, or None."""
        return _cache_show.get(str(simkl_id))

    def get_movie_details_full(self, simkl_id: int):
        key = str(simkl_id)
        cached = _cache_movie.get(key)
        if cached is not None:
            return cached
        data = self._get(f"/movies/{simkl_id}", params={"extended": "full"}, auth=False)
        _cache_movie.set(key, data)
        return data

    def get_movie_details_if_cached(self, simkl_id: int):
        """Returns cached movie details without a network call, or None."""
        return _cache_movie.get(str(simkl_id))

    def search_shows(self, query: str):
        """GET /search/tv — not cached (user-initiated search). Returns a list of results."""
        data = self._get("/search/tv", params={"q": query}, auth=False)
        if self.debug:
            xbmc.log(f"[WhatsOnStreamer][SIMKL] search_shows({query!r}) -> {data}", xbmc.LOGINFO)
        return data if isinstance(data, list) else []

    def search_movies(self, query: str):
        """GET /search/movie — not cached (user-initiated search). Returns a list of results."""
        data = self._get("/search/movie", params={"q": query}, auth=False)
        if self.debug:
            xbmc.log(f"[WhatsOnStreamer][SIMKL] search_movies({query!r}) -> {data}", xbmc.LOGINFO)
        return data if isinstance(data, list) else []

    # -------------------------
    # PIN / device flow (already working)
    # -------------------------
    def request_pin(self):
        # GET /oauth/pin?client_id=... [1](https://forums.trakt.tv/t/next-episode-missing-from-watched-progress-on-api/88273)
        return self._get("/oauth/pin", params={"client_id": self.client_id}, auth=False)

    def poll_pin(self, user_code: str, interval: int, expires_in: int, progress_cb=None, cancel_fn=None):
        # Poll GET /oauth/pin/{user_code}
        deadline = time.time() + int(expires_in)
        wait = max(1, int(interval))

        while time.time() < deadline:
            if cancel_fn and cancel_fn():
                return None

            if progress_cb:
                remaining = int(deadline - time.time())
                progress_cb(remaining)

            res = self._get(f"/oauth/pin/{user_code}", auth=False)
            token = res.get("access_token") or res.get("token")
            if token:
                return token

            # Sleep in short ticks so cancellation is responsive
            slept = 0
            while slept < wait:
                if cancel_fn and cancel_fn():
                    return None
                time.sleep(0.25)
                slept += 0.25

        return None

    # -------------------------
    # Step 4A: Watching items
    # -------------------------
    def get_watching_shows(self):
        return self._get("/sync/all-items/shows/watching", params={"extended": "full"}, auth=True)

    def get_completed_shows(self):
        return self._get("/sync/all-items/shows/completed", params={"extended": "full"}, auth=True)

    def is_show_completed(self, simkl_id: int) -> bool:
        """True if the whole show is on SIMKL's completed list — no per-episode
        breakdown exists there, so callers should treat every episode as watched
        rather than calling get_watched_episodes for a season number."""
        cached = self._watching_and_completed()
        return any(
            ((it.get("show") or {}).get("ids") or {}).get("simkl") == simkl_id
            for it in cached["completed"]
        )

    def get_watched_episodes(self, simkl_id: int, season: int) -> set:
        """Episode numbers SIMKL already has marked watched for this show/season,
        so the season screen can show a native Kodi playcount checkmark. Reuses
        the same "watching" sync as WhatsUpNext, cached briefly since it's an
        account-wide fetch that would otherwise re-run on every season browse.
        """
        cached = self._watching_and_completed()
        for it in cached["watching"]:
            if ((it.get("show") or {}).get("ids") or {}).get("simkl") != simkl_id:
                continue
            for s in it.get("seasons") or []:
                if s.get("number") == season:
                    return {e["number"] for e in (s.get("episodes") or []) if "number" in e}
        return set()

    def _watching_and_completed(self) -> dict:
        cache_key = "watching-and-completed"
        cached = _cache_watching.get(cache_key)
        if cached is not None:
            return cached
        watching = self.get_watching_shows()
        completed = self.get_completed_shows()
        cached = {
            "watching": (watching or {}).get("shows") or [],
            "completed": (completed or {}).get("shows") or [],
        }
        _cache_watching.set(cache_key, cached)
        return cached

    def get_plan_movies(self):
        """
        Fetch movies from the user's SIMKL Plan to Watch list.
        """
        data = self._get("/sync/all-items/movies/plan", auth=True)
        return data

    def get_completed_movies(self):
        return self._get("/sync/all-items/movies/completed", auth=True)

    def get_dropped_shows(self):
        return self._get("/sync/all-items/shows/dropped", auth=True)

    def get_dropped_movies(self):
        return self._get("/sync/all-items/movies/dropped", auth=True)

    def _set_list_status(self, kind: str, simkl_id: int, status: str) -> dict:
        """POST /sync/add-to-list — moves a show or movie into the given watchlist
        status. `kind` is 'show' or 'movie'; `status` is one of watching/plantowatch/
        hold/completed/dropped (movies only support plantowatch/completed/dropped)."""
        key = "shows" if kind == "show" else "movies"
        body = {key: [{"to": status, "ids": {"simkl": int(simkl_id)}}]}
        return self._post("/sync/add-to-list", body, auth=True)

    def add_to_dropped(self, kind: str, simkl_id: int) -> dict:
        return self._set_list_status(kind, simkl_id, "dropped")

    def mark_watched(self, kind: str, simkl_id: int) -> dict:
        """POST /sync/history — the actual watch-history endpoint (same one
        script.simkl's own scrobbler uses), not /sync/add-to-list. Confirmed
        live: add-to-list's 'completed' status moves the item's list bucket but
        does NOT reliably create real episode watch history for a show - the
        episode stayed unmarked despite the show showing as 'completed'.
        Omitting "seasons" for a show tells SIMKL to mark every aired episode
        watched, same as scrobbling through the whole thing."""
        key = "shows" if kind == "show" else "movies"
        body = {key: [{"ids": {"simkl": int(simkl_id)}}]}
        result = self._post("/sync/history", body, auth=True)
        _cache_watching.clear()
        return result

    def mark_episode_watched(self, simkl_id: int, season: int, episode: int) -> dict:
        """POST /sync/history for one specific episode - same shape script.simkl's
        own scrobbler sends, see mark_watched()."""
        body = {"shows": [{"ids": {"simkl": int(simkl_id)}, "seasons": [
            {"number": int(season), "episodes": [{"number": int(episode)}]}
        ]}]}
        result = self._post("/sync/history", body, auth=True)
        _cache_watching.clear()
        return result

    def mark_season_watched(self, simkl_id: int, season: int, episode_numbers) -> dict:
        """POST /sync/history for every episode number given in one season."""
        body = {"shows": [{"ids": {"simkl": int(simkl_id)}, "seasons": [
            {"number": int(season), "episodes": [{"number": int(e)} for e in episode_numbers]}
        ]}]}
        result = self._post("/sync/history", body, auth=True)
        _cache_watching.clear()
        return result

    def add_to_watchlist(self, kind: str, simkl_id: int) -> dict:
        """Shows go to 'watching' (actively tracking); movies can't use that status
        (SIMKL only allows plantowatch/completed/dropped for movies), so they go to
        'plantowatch' - SIMKL's literal watchlist status."""
        status = "watching" if kind == "show" else "plantowatch"
        return self._set_list_status(kind, simkl_id, status)