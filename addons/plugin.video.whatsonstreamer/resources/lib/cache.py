import json
import os
import time

try:
    import xbmcaddon
    import xbmcvfs
    # Explicit id, not the parameterless xbmcaddon.Addon() - that relies on Kodi
    # auto-inferring the running addon, which works for plugin:// invocations and
    # the long-running service loop, but throws "No valid addon id could be
    # obtained" when this module is imported from a one-shot
    # RunScript(service.py,<args>) invocation of an arbitrary file path (exactly
    # how the WhatsUpNext manual cache-warm Tools action runs service.py).
    def _cache_dir():
        profile = xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.whatsonstreamer').getAddonInfo("profile"))
        return os.path.join(profile, "cache")
except ImportError:
    def _cache_dir():
        return os.path.join(os.path.dirname(__file__), ".cache")


class DiskCache:
    """
    Simple per-namespace JSON cache with per-entry TTL.
    One file per namespace; safe for single-process use (Kodi plugin model).
    """

    def __init__(self, name, ttl):
        self._ttl = ttl
        d = _cache_dir()
        os.makedirs(d, exist_ok=True)
        self._path = os.path.join(d, f"{name}.json")
        self._data = None  # lazy load

    # ------------------------------------------------------------------
    def _load(self):
        if self._data is not None:
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except Exception:
            self._data = {}

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def get(self, key):
        self._load()
        entry = self._data.get(str(key))
        if entry and entry.get("exp", 0) > time.time():
            return entry["v"]
        return None

    def set(self, key, value):
        self._load()
        self._data[str(key)] = {"exp": time.time() + self._ttl, "v": value}
        self._save()

    def clear(self):
        self._data = {}
        self._save()
