import json
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import xbmc
from resources.lib.cache import DiskCache

TMDB_API_BASE = "https://api.themoviedb.org/3"

_cache_season = DiskCache("tmdb_season", ttl=86400)  # 24 h — episode lists rarely change
_cache_tv     = DiskCache("tmdb_tv",     ttl=172800) # 2 days
_cache_movie  = DiskCache("tmdb_movie",  ttl=86400)  # 24 h
_cache_discover_movie = DiskCache("tmdb_discover_movie", ttl=172800)  # 2 days — genre charts move slowly
_cache_discover_tv    = DiskCache("tmdb_discover_tv",    ttl=172800)  # 2 days

# Minimum vote_count for a discover result to count as "top rated" rather than
# a handful of 10/10 votes on an obscure title. TV titles get far fewer votes
# than movies on TMDB, hence the lower bar.
_DISCOVER_MOVIE_MIN_VOTES = 300
_DISCOVER_TV_MIN_VOTES = 100


class TmdbApi:
    def __init__(self, addon):
        self.addon = addon
        self.api_key = addon.getSettingString("tmdb_api_key").strip()
        self.debug = addon.getSettingBool("debug_logging")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def movie_details_if_cached(self, tmdb_movie_id: int, language="en-US"):
        """Returns cached movie details without making a network call, or None."""
        return _cache_movie.get(f"{tmdb_movie_id}:{language}")

    def movie_details(self, tmdb_movie_id: int, language="en-US"):
        key = f"{tmdb_movie_id}:{language}"
        cached = _cache_movie.get(key)
        if cached is not None:
            return cached
        data = self._get(f"/movie/{int(tmdb_movie_id)}", params={"language": language, "append_to_response": "credits"})
        _cache_movie.set(key, data)
        return data

    def prefetch_details(self, kind, tmdb_ids, language="en-US", workers=8):
        """Warms the movie/tv details cache (which includes credits) for many ids
        at once, so a list of ~100 genre titles doesn't make 100 sequential
        requests. Only the network fetches run in threads; the cache is
        written once afterwards from the calling thread (DiskCache isn't
        thread-safe). Failures are skipped - callers fall back to no cast."""
        movie = kind == "movie"
        cache = _cache_movie if movie else _cache_tv
        path = "/movie/{}" if movie else "/tv/{}"
        missing = [i for i in dict.fromkeys(tmdb_ids) if i and cache.get(f"{i}:{language}") is None]
        if not missing:
            return

        def fetch(tid):
            try:
                return tid, self._get(path.format(int(tid)), params={"language": language, "append_to_response": "credits"})
            except Exception as e:
                xbmc.log(f"[WhatsOnStreamer][TMDB] prefetch {kind} {tid} failed: {e}", xbmc.LOGERROR)
                return tid, None

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(fetch, missing))
        cache.set_many({f"{tid}:{language}": data for tid, data in results if data})

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
                xbmc.log(f"[WhatsOnStreamer][TMDB] GET {url} -> {raw[:1200]}", xbmc.LOGINFO)
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
        data = self._get(f"/tv/{int(tmdb_tv_id)}", params={"language": language, "append_to_response": "credits"})
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

    def discover_movies(self, genre_id: int, page: int = 1, language="en-US", without_genres=None):
        """GET /discover/movie for one genre, sorted top-rated first (see
        _DISCOVER_MOVIE_MIN_VOTES for why a vote-count floor is applied).
        `without_genres` excludes a genre that would otherwise double up with
        its own dedicated genre bucket (e.g. an animated sci-fi film showing
        up under both Animation and Science Fiction)."""
        key = f"{genre_id}:{page}:{language}:{without_genres}"
        cached = _cache_discover_movie.get(key)
        if cached is not None:
            return cached
        params = {
            "language": language,
            "with_genres": genre_id,
            "with_original_language": "en",
            "sort_by": "vote_average.desc",
            "vote_count.gte": _DISCOVER_MOVIE_MIN_VOTES,
            "page": page,
        }
        if without_genres:
            params["without_genres"] = without_genres
        data = self._get("/discover/movie", params=params)
        _cache_discover_movie.set(key, data)
        return data

    def discover_tv(self, genre_id: int, page: int = 1, language="en-US", without_genres=None):
        """GET /discover/tv for one genre, sorted top-rated first. See
        discover_movies() for what `without_genres` is for."""
        key = f"{genre_id}:{page}:{language}:{without_genres}"
        cached = _cache_discover_tv.get(key)
        if cached is not None:
            return cached
        params = {
            "language": language,
            "with_genres": genre_id,
            "with_original_language": "en",
            "sort_by": "vote_average.desc",
            "vote_count.gte": _DISCOVER_TV_MIN_VOTES,
            "page": page,
        }
        if without_genres:
            params["without_genres"] = without_genres
        data = self._get("/discover/tv", params=params)
        _cache_discover_tv.set(key, data)
        return data
