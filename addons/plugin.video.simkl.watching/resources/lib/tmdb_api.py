import json
import urllib.parse
import urllib.request
import xbmc

TMDB_API_BASE = "https://api.themoviedb.org/3"


class TmdbApi:
    def __init__(self, addon):
        self.addon = addon
        self.api_key = addon.getSettingString("tmdb_api_key").strip()
        self.debug = addon.getSettingBool("debug_logging")

        # Simple in-memory cache to reduce calls during a single run
        self._season_cache = {}  # (tv_id, season_number, language) -> dict
        self._tv_cache = {}      # (tv_id, language) -> dict

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path, params=None):
        if not self.api_key:
            raise RuntimeError("Missing TMDB API key in add-on settings.")

        params = dict(params or {})
        # TMDB v3 auth: api_key query parameter is supported. [1](https://www.rdocumentation.org/packages/TMDb/versions/1.1/topics/tv_season)[2](https://mcp-link.vercel.app/links/tmdb)
        params["api_key"] = self.api_key

        url = TMDB_API_BASE + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"accept": "application/json"})

        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
            if self.debug:
                xbmc.log(f"[SIMKL Watching][TMDB] GET {url} -> {raw[:1200]}", xbmc.LOGINFO)
            return json.loads(raw)

    # -------------------------
    # TV endpoints
    # -------------------------

    def tv_details(self, tmdb_tv_id: int, language="en-US"):
        """
        GET /tv/{series_id}
        Response includes next_episode_to_air (when known). [3](https://adamayoung.github.io/TMDb/documentation/tmdb/)
        """
        key = (int(tmdb_tv_id), language)
        if key in self._tv_cache:
            return self._tv_cache[key]

        data = self._get(f"/tv/{int(tmdb_tv_id)}", params={"language": language})
        self._tv_cache[key] = data
        return data

    def tv_season(self, tmdb_tv_id: int, season_number: int, language="en-US"):
        """
        GET /tv/{series_id}/season/{season_number}
        Season response includes episodes list with air_date fields. [4](https://developer.themoviedb.org/reference/search-tv)
        """
        key = (int(tmdb_tv_id), int(season_number), language)
        if key in self._season_cache:
            return self._season_cache[key]

        data = self._get(
            f"/tv/{int(tmdb_tv_id)}/season/{int(season_number)}",
            params={"language": language}
        )
        self._season_cache[key] = data
        return data

    def episode_air_date(self, tmdb_tv_id: int, season_number: int, episode_number: int, language="en-US"):
        """
        Fetch season details and return air_date for a specific episode.
        """
        season = self.tv_season(tmdb_tv_id, season_number, language=language)
        for ep in season.get("episodes", []) or []:
            if ep.get("episode_number") == int(episode_number):
                return ep.get("air_date")
        return None

    def next_episode_air_date(self, tmdb_tv_id: int, language="en-US"):
        """
        Uses tv details to find next_episode_to_air.air_date when present. [3](https://adamayoung.github.io/TMDb/documentation/tmdb/)
        """
        details = self.tv_details(tmdb_tv_id, language=language)
        nxt = details.get("next_episode_to_air")
        if isinstance(nxt, dict):
            return nxt.get("air_date")
        return None