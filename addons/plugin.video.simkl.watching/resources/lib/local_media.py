import os
import re
from resources.lib.cache import DiskCache

try:
    import xbmc
    import xbmcaddon
    def _log(msg):
        xbmc.log(f"[SIMKL Watching][LocalMedia] {msg}", xbmc.LOGINFO)
except ImportError:
    def _log(msg):
        print(f"[LocalMedia] {msg}")

_cache_lf = DiskCache("local_media", ttl=120)  # 2 min

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts"}


def _se_from_filename(filename):
    m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _is_video(filename):
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return ext in _VIDEO_EXTS


def _normalize(title):
    s = str(title).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class LocalMediaApi:

    def __init__(self):
        try:
            self._base_path = xbmcaddon.Addon().getSettingString("local_media_path").strip()
        except Exception:
            self._base_path = ""

    def is_configured(self):
        return bool(self._base_path) and os.path.isdir(self._base_path)

    def _find_show_dir(self, show_title):
        norm_target = _normalize(show_title)
        try:
            entries = os.listdir(self._base_path)
        except OSError:
            return None
        best, best_score = None, 0.0
        for entry in entries:
            full = os.path.join(self._base_path, entry)
            if not os.path.isdir(full):
                continue
            norm_entry = _normalize(entry)
            if norm_entry == norm_target:
                return full
            if norm_target in norm_entry or norm_entry in norm_target:
                score = len(min(norm_target, norm_entry, key=len)) / max(len(norm_target), len(norm_entry), 1)
                if score > best_score:
                    best_score, best = score, full
        if best and best_score >= 0.7:
            return best
        return None

    def _find_season_dir(self, show_dir, season):
        candidates = [
            f"Season {season:02d}",
            f"Season {season}",
            f"S{season:02d}",
        ]
        try:
            entries = os.listdir(show_dir)
        except OSError:
            return None
        for candidate in candidates:
            for entry in entries:
                if entry.lower() == candidate.lower():
                    return os.path.join(show_dir, entry)
        return None

    def get_available_episodes(self, show_title, season):
        cache_key = f"{show_title}:{season}"
        cached = _cache_lf.get(cache_key)
        if cached is not None:
            _log(f"Local episodes from cache for '{show_title}' S{season:02d}: {sorted(cached)}")
            return set(cached)
        show_dir = self._find_show_dir(show_title)
        if not show_dir:
            _log(f"No local folder for '{show_title}'")
            return set()
        season_dir = self._find_season_dir(show_dir, int(season))
        if not season_dir:
            _log(f"No Season {season} folder in '{show_dir}'")
            return set()
        available = set()
        try:
            for fname in os.listdir(season_dir):
                if _is_video(fname):
                    se = _se_from_filename(fname)
                    if se and se[0] == int(season):
                        available.add(se[1])
        except OSError as e:
            _log(f"Error reading season dir: {e}")
        _log(f"Local episodes for '{show_title}' S{season:02d}: {sorted(available)}")
        _cache_lf.set(cache_key, list(available))
        return available

    def find_episode(self, show_title, season, episode):
        show_dir = self._find_show_dir(show_title)
        if not show_dir:
            return None
        season_dir = self._find_season_dir(show_dir, int(season))
        if not season_dir:
            return None
        target = (int(season), int(episode))
        try:
            for fname in os.listdir(season_dir):
                if _is_video(fname) and _se_from_filename(fname) == target:
                    return os.path.join(season_dir, fname)
        except OSError:
            return None
        return None
