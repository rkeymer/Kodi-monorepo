import json
import urllib.parse

from resources.lib.cache import DiskCache
from resources.lib.iptv_http import fetch_url
from resources.lib.alldebrid_api import _normalize, _score

try:
    import xbmc
    import xbmcaddon
    def _log(msg):
        xbmc.log(f"[WhatsOnStreamer][IPTV] {msg}", xbmc.LOGINFO)
except ImportError:
    def _log(msg):
        print(f"[IPTV] {msg}")

# get_series_info is fetched lazily, one series at a time, only when a season
# is actually opened — mirrors tmdb_api.py's tv_season() 24h cache. The
# catalog caches (series list) use the user-configurable iptv_catalog_ttl_hours
# setting instead, since a full catalog re-scan is cheap but shouldn't run on
# every browse.
_SERIES_INFO_TTL = 86400  # 24h


class IptvApi:

    def __init__(self):
        try:
            addon = xbmcaddon.Addon()
            self._base_url = addon.getSettingString("iptv_base_url").strip().rstrip("/")
            self._username = addon.getSettingString("iptv_username").strip()
            self._password = addon.getSettingString("iptv_password").strip()
            self._series_enabled = addon.getSettingBool("iptv_series_enabled")
            self._vod_enabled = addon.getSettingBool("iptv_vod_enabled")
            ttl_hours = addon.getSettingInt("iptv_catalog_ttl_hours") or 24
        except Exception:
            self._base_url = ""
            self._username = ""
            self._password = ""
            self._series_enabled = True
            self._vod_enabled = True
            ttl_hours = 24

        self._cache_series = DiskCache("iptv_series", ttl=max(1, ttl_hours) * 3600)
        self._cache_series_info = DiskCache("iptv_series_info", ttl=_SERIES_INFO_TTL)
        self._cache_vod = DiskCache("iptv_vod", ttl=max(1, ttl_hours) * 3600)

    def is_configured(self):
        return bool(self._series_enabled and self._base_url and self._username and self._password)

    def is_vod_configured(self):
        return bool(self._vod_enabled and self._base_url and self._username and self._password)

    # ------------------------------------------------------------------
    def _player_api(self, action, **params):
        query = {"username": self._username, "password": self._password, "action": action}
        query.update({k: v for k, v in params.items() if v is not None})
        url = f"{self._base_url}/player_api.php?{urllib.parse.urlencode(query)}"
        raw = fetch_url(url, timeout=60, retries=3)
        return json.loads(raw.decode("utf-8", errors="replace"))

    # ------------------------------------------------------------------
    # Download-time: one bounded call for the whole series catalog
    # (names/ids only — no per-series episode breakdown here).
    # ------------------------------------------------------------------
    def get_series_catalog(self, force=False):
        if not force:
            cached = self._cache_series.get("all")
            if cached is not None:
                return cached
        try:
            series = self._player_api("get_series")
            if not isinstance(series, list):
                series = []
        except Exception as e:
            _log(f"get_series_catalog failed: {e}")
            series = []
        _log(f"Series catalog fetched: {len(series)} series")
        self._cache_series.set("all", series)
        return series

    # ------------------------------------------------------------------
    # Browse-time: resolve a SIMKL show title to a series_id using the
    # already-cached catalog — pure in-memory fuzzy match, no network.
    # ------------------------------------------------------------------
    def find_series_id(self, show_title):
        norm_title = _normalize(show_title)
        best_score, best_id = 0.0, None
        for item in self.get_series_catalog():
            score = _score(norm_title, _normalize(item.get("name", "")))
            if score > best_score:
                best_score, best_id = score, item.get("series_id")
        return best_id if best_score >= 0.55 else None

    # ------------------------------------------------------------------
    # Browse-time: ONE get_series_info call per opened season, cached 24h —
    # mirrors tmdb_api.py's lazy tv_season() fetch.
    # ------------------------------------------------------------------
    def get_series_info(self, series_id):
        cached = self._cache_series_info.get(series_id)
        if cached is not None:
            return cached
        try:
            info = self._player_api("get_series_info", series_id=series_id)
            if not isinstance(info, dict):
                info = {}
        except Exception as e:
            _log(f"get_series_info({series_id}) failed: {e}")
            info = {}
        self._cache_series_info.set(series_id, info)
        return info

    def _season_episodes(self, show_title, season):
        series_id = self.find_series_id(show_title)
        if series_id is None:
            return []
        episodes = (self.get_series_info(series_id) or {}).get("episodes") or {}
        return episodes.get(str(season)) or []

    def get_available_episodes(self, show_title, season):
        """Same shape as AllDebridApi.get_available_episodes: set of ep numbers."""
        available = set()
        for ep in self._season_episodes(show_title, season):
            try:
                available.add(int(ep.get("episode_num")))
            except (TypeError, ValueError):
                continue
        _log(f"Available IPTV episodes for '{show_title}' S{season:02d}: {sorted(available)}")
        return available

    def find_episode(self, show_title, season, episode):
        """Same shape as AllDebridApi.find_episode: (stream_url, display_name) or (None, None)."""
        target = int(episode)
        for ep in self._season_episodes(show_title, season):
            try:
                ep_num = int(ep.get("episode_num"))
            except (TypeError, ValueError):
                continue
            if ep_num != target:
                continue
            stream_id = ep.get("id")
            if not stream_id:
                continue
            ext = ep.get("container_extension") or "mp4"
            display = ep.get("title") or f"{show_title} S{season:02d}E{target:02d}"
            stream_url = f"{self._base_url}/series/{self._username}/{self._password}/{stream_id}.{ext}"
            return stream_url, display
        return None, None

    # ------------------------------------------------------------------
    # Movies (Xtream VOD) — mirrors the series lookup above, just without
    # the season/episode breakdown: one catalog entry per movie.
    # ------------------------------------------------------------------
    def get_vod_catalog(self, force=False):
        if not force:
            cached = self._cache_vod.get("all")
            if cached is not None:
                return cached
        try:
            vod = self._player_api("get_vod_streams")
            if not isinstance(vod, list):
                vod = []
        except Exception as e:
            _log(f"get_vod_catalog failed: {e}")
            vod = []
        _log(f"VOD catalog fetched: {len(vod)} movies")
        self._cache_vod.set("all", vod)
        return vod

    def find_vod_item(self, movie_title):
        norm_title = _normalize(movie_title)
        best_score, best_item = 0.0, None
        for item in self.get_vod_catalog():
            score = _score(norm_title, _normalize(item.get("name", "")))
            if score > best_score:
                best_score, best_item = score, item
        return best_item if best_score >= 0.55 else None

    def is_movie_available(self, movie_title):
        return self.find_vod_item(movie_title) is not None

    def find_movie(self, movie_title):
        """Same shape as AllDebridApi.find_movie: (stream_url, display_name) or (None, None)."""
        item = self.find_vod_item(movie_title)
        if not item:
            return None, None
        stream_id = item.get("stream_id")
        if not stream_id:
            return None, None
        ext = item.get("container_extension") or "mp4"
        display = item.get("name") or movie_title
        stream_url = f"{self._base_url}/movie/{self._username}/{self._password}/{stream_id}.{ext}"
        return stream_url, display
