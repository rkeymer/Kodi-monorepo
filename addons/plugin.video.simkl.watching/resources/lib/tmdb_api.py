import json
import urllib.parse
import urllib.request
import xbmc
from resources.lib.cache import DiskCache

TMDB_API_BASE = "https://api.themoviedb.org/3"

_cache_season = DiskCache("tmdb_season", ttl=86400)  # 24 h — episode lists rarely change
_cache_tv     = DiskCache("tmdb_tv",     ttl=172800) # 2 days
_cache_movie  = DiskCache("tmdb_movie",  ttl=86400)  # 24 h


class TmdbApi:
    def __init__(self, addon):
        self.addon = addon
        self.api_key = addon.getSettingString("tmdb_api_key").strip()
        self.debug = addon.getSettingBool("debug_logging")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def movie_details(self, tmdb_movie_id: int, language="en-US"):
        key = f"{tmdb_movie_id}:{language}"
        cached = _cache_movie.get(key)
        if cached is not None:
            return cached
        data = self._get(f"/movie/{int(tmdb_movie_id)}", params={"language": language})
        _cache_movie.set(key, data)
        return data

    def _get(self, path, params=None):
        if not self.api_key:
            raise RuntimeError("Missing TMDB API key in add-on settings.")

        params = dict(params or {})
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

    def tv_details_if_cached(self, tmdb_tv_id: int, language="en-US"):
        """Returns cached TV details without making a network call, or None."""
        return _cache_tv.get(f"{tmdb_tv_id}:{language}")

    def tv_details(self, tmdb_tv_id: int, language="en-US"):
        key = f"{tmdb_tv_id}:{language}"
        cached = _cache_tv.get(key)
        if cached is not None:
            return cached
        data = self._get(f"/tv/{int(tmdb_tv_id)}", params={"language": language})
        _cache_tv.set(key, data)
        return data

    def tv_season(self, tmdb_tv_id: int, season_number: int, language="en-US"):
        key = f"{tmdb_tv_id}:{season_number}:{language}"
        cached = _cache_season.get(key)
        if cached is not None:
            return cached
        data = self._get(
            f"/tv/{int(tmdb_tv_id)}/season/{int(season_number)}",
            params={"language": language}
        )
        _cache_season.set(key, data)
        return data

    def episode_air_date(self, tmdb_tv_id: int, season_number: int, episode_number: int, language="en-US"):
        season = self.tv_season(tmdb_tv_id, season_number, language=language)
        for ep in season.get("episodes", []) or []:
            if ep.get("episode_number") == int(episode_number):
                return ep.get("air_date")
        return None

    def next_episode_air_date(self, tmdb_tv_id: int, language="en-US"):
        details = self.tv_details(tmdb_tv_id, language=language)
        nxt = details.get("next_episode_to_air")
        if isinstance(nxt, dict):
            return nxt.get("air_date")
        return None

    def search_tv(self, query: str, language="en-US"):
        """GET /search/tv — not cached (user-initiated search)"""
        return self._get("/search/tv", params={"query": query, "language": language})

    def tv_external_ids(self, tmdb_tv_id: int):
        """GET /tv/{id}/external_ids — returns imdb_id, tvdb_id etc."""
        return self._get(f"/tv/{int(tmdb_tv_id)}/external_ids")
