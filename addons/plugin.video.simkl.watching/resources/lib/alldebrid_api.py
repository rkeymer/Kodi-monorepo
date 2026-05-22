import re
import json
import urllib.request
import urllib.parse
from difflib import SequenceMatcher

try:
    import xbmc
    def _log(msg):
        xbmc.log(f"[SIMKL Watching][AllDebrid] {msg}", xbmc.LOGINFO)
except ImportError:
    def _log(msg):
        print(f"[AllDebrid] {msg}")

# TODO: move to addon settings before release
_API_KEY = "TiSKYaxF1f1jun2fbjL5"
_BASE_V4  = "https://api.alldebrid.com/v4"
_BASE_V41 = "https://api.alldebrid.com/v4.1"
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
    s = re.sub(r"\[.*?\]", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"S\d{1,2}E\d{1,2}", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"S\d{1,2}\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bSeason\s*\d+\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d{4}\b", " ", s)
    s = _NOISE.sub(" ", s)
    s = re.sub(r"^[\w.]+\.\w{2,4}\s*[-–]\s*", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _se_from_filename(filename):
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


def _flatten_tree(nodes):
    """Recursively flatten AllDebrid file tree into (filename, link) tuples."""
    for node in nodes:
        if "e" in node:
            yield from _flatten_tree(node["e"])
        elif "l" in node:
            yield node["n"], node["l"]


class AllDebridApi:

    def _post(self, base, path, data=None):
        params = {"agent": _AGENT, "apikey": _API_KEY}
        if data:
            params.update(data)
        url = f"{base}/{path}"
        encoded = urllib.parse.urlencode(params, doseq=True).encode()
        req = urllib.request.Request(
            url, data=encoded,
            headers={"User-Agent": _AGENT, "Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def _get_all_magnets(self):
        resp = self._post(_BASE_V41, "magnet/status")
        _log(f"magnet/status v4.1: status={resp.get('status')} | data keys={list(resp.get('data', {}).keys())}")
        if resp.get("status") != "success":
            _log(f"API error: {resp.get('error', resp)}")
            return []
        magnets = resp.get("data", {}).get("magnets", [])
        _log(f"Total magnets returned: {len(magnets)}")
        return magnets

    def _get_magnet_files(self, magnet_id):
        resp = self._post(_BASE_V4, "magnet/files", {"id[]": str(magnet_id)})
        if resp.get("status") != "success":
            _log(f"magnet/files error for {magnet_id}: {resp.get('error', resp)}")
            return []
        magnets = resp.get("data", {}).get("magnets", [])
        if not magnets:
            return []
        files = list(_flatten_tree(magnets[0].get("files", [])))
        _log(f"magnet {magnet_id} files: {[f[0] for f in files]}")
        return files  # list of (filename, link)

    def unlock_link(self, link):
        resp = self._post(_BASE_V4, "link/unlock", {"link": link})
        if resp.get("status") != "success":
            _log(f"unlock error: {resp.get('error', resp)}")
            return None
        return resp.get("data", {}).get("link")

    def get_available_episodes(self, show_title, season):
        """
        Returns a set of episode numbers available in AllDebrid for the given show/season.
        Fetches magnet list once, then file lists only for title-matching magnets.
        """
        norm_title = _normalize(show_title)
        target_season = int(season)

        magnets = self._get_all_magnets()
        scored = []
        for mag in magnets:
            s = _score(norm_title, _normalize(mag.get("filename", "")))
            if s >= 0.55:
                scored.append((s, mag))

        scored.sort(key=lambda x: -x[0])
        available = set()

        for _, mag in scored[:6]:  # cap at 6 magnets to keep it fast
            files = self._get_magnet_files(mag["id"])
            for fname, _ in files:
                se = _se_from_filename(fname)
                if se and se[0] == target_season and _is_video(fname):
                    available.add(se[1])

        _log(f"Available episodes for '{show_title}' S{season:02d}: {sorted(available)}")
        return available

    def find_episode(self, show_title, season, episode):
        """
        Search saved magnets for a matching episode.
        Returns (streaming_url, display_filename) or (None, None).
        """
        norm_title = _normalize(show_title)
        target = (int(season), int(episode))

        magnets = self._get_all_magnets()
        _log(f"Searching for '{norm_title}' S{season:02d}E{episode:02d} in {len(magnets)} magnets")

        scored = []
        for mag in magnets:
            norm_mag = _normalize(mag.get("filename", ""))
            s = _score(norm_title, norm_mag)
            if s >= 0.55:
                scored.append((s, mag))

        _log(f"Title candidates: {[(round(s, 2), m['filename']) for s, m in scored]}")

        if not scored:
            return None, None

        scored.sort(key=lambda x: -x[0])
        for title_score, mag in scored:
            files = self._get_magnet_files(mag["id"])
            for fname, link in files:
                se = _se_from_filename(fname)
                if _is_video(fname) and se == target:
                    _log(f"Episode match: '{fname}' in magnet '{mag['filename']}'")
                    stream_url = self.unlock_link(link)
                    _log(f"Stream URL: {'ok' if stream_url else 'FAILED'}")
                    if stream_url:
                        return stream_url, fname.rsplit("/", 1)[-1]

        return None, None
