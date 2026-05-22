import re
import json
import urllib.request
import urllib.parse
from difflib import SequenceMatcher

# TODO: move to addon settings before release
_API_KEY = "TiSKYaxF1f1jun2fbjL5"
_BASE = "https://api.alldebrid.com/v4"
_AGENT = "plugin.video.simkl.watching"

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts"}

_NOISE = re.compile(
    r"\b("
    r"2160p?|1080p?|720p?|480p?|4k|uhd|hdr|sdr"
    r"|web[-.]?rip|web[-.]?dl|blu[-.]?ray|bdremux|remux"
    r"|hevc|avc|x264|x265|h264|h265|xvid|divx"
    r"|aac|ac3|dts|ddp?5?\.?1?|atmos|eac3|truehd"
    r"|amzn|nf|hulu|dsnp|hmax|atvp|pcok|itvx"
    r"|proper|repack|extended|theatrical|unrated"
    r"|10bit|8bit|mp4|mkv|avi"
    r")\b",
    re.IGNORECASE,
)


def _normalize(s):
    s = str(s)
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\[.*?\]", " ", s)        # remove [release groups]
    s = re.sub(r"\([^)]*\)", " ", s)       # remove (anything in parens)
    s = re.sub(r"S\d{1,2}E\d{1,2}", " ", s, flags=re.IGNORECASE)  # SxxExx
    s = re.sub(r"S\d{1,2}\b", " ", s, flags=re.IGNORECASE)         # Sxx alone
    s = re.sub(r"\bSeason\s*\d+\b", " ", s, flags=re.IGNORECASE)   # Season N
    s = re.sub(r"\b\d{4}\b", " ", s)       # years
    s = _NOISE.sub(" ", s)
    # strip leading junk like "www.site.org - "
    s = re.sub(r"^[\w.]+\.\w{2,4}\s*[-–]\s*", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _se_from_filename(filename):
    """Return (season, episode) ints or None."""
    m = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _is_video(filename):
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return ext in _VIDEO_EXTS


def _score(norm_title, norm_mag):
    if norm_title == norm_mag:
        return 1.0
    if norm_title in norm_mag:
        return 0.85 + 0.15 * (len(norm_title) / max(len(norm_mag), 1))
    if norm_mag in norm_title:
        return 0.75
    return SequenceMatcher(None, norm_title, norm_mag).ratio()


class AllDebridApi:

    def _get(self, path, params=None):
        p = {"agent": _AGENT, "apikey": _API_KEY}
        if params:
            p.update(params)
        url = f"{_BASE}/{path}?{urllib.parse.urlencode(p)}"
        req = urllib.request.Request(url, headers={"User-Agent": _AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def _get_all_magnets(self):
        resp = self._get("magnet/status")
        if resp.get("status") != "success":
            return []
        return resp.get("data", {}).get("magnets", [])

    def _get_magnet_files(self, magnet_id):
        resp = self._get("magnet/status", {"id": str(magnet_id)})
        if resp.get("status") != "success":
            return []
        magnets = resp.get("data", {}).get("magnets", [])
        if not magnets:
            return []
        return magnets[0].get("links", [])

    def unlock_link(self, link):
        resp = self._get("link/unlock", {"link": link})
        if resp.get("status") != "success":
            return None
        return resp.get("data", {}).get("link")

    def find_episode(self, show_title, season, episode):
        """
        Search saved magnets for a matching episode.
        Returns (streaming_url, display_filename) or (None, None).
        """
        norm_title = _normalize(show_title)
        target = (int(season), int(episode))

        magnets = self._get_all_magnets()

        # Score every magnet against the show title
        scored = []
        for mag in magnets:
            s = _score(norm_title, _normalize(mag.get("filename", "")))
            if s >= 0.55:
                scored.append((s, mag))

        if not scored:
            return None, None

        # Search title-matching magnets for the episode file
        scored.sort(key=lambda x: -x[0])
        for title_score, mag in scored:
            files = mag.get("links") or self._get_magnet_files(mag["id"])
            for f in files:
                fname = f.get("filename", "")
                if not _is_video(fname):
                    continue
                if _se_from_filename(fname) == target:
                    stream_url = self.unlock_link(f["link"])
                    if stream_url:
                        return stream_url, fname.rsplit("/", 1)[-1]

        return None, None
