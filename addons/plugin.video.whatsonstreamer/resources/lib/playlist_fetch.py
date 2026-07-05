import io

from resources.lib.iptv_http import fetch_url
from resources.lib import log
from resources.lib.m3u import iter_m3u

# fetch_url is re-exported from iptv_http so callers can keep doing
# `from resources.lib.playlist_fetch import fetch_url, parse_and_index` —
# this dedups what used to be two near-identical retry/backoff HTTP fetchers
# (one in WhatsOnNow, one already in WhatsOnStreamer's iptv_http.py).


def parse_and_index(m3u_bytes: bytes, filter_fn=None, drop_vod: bool = True) -> dict:
    text = m3u_bytes.decode('utf-8', errors='replace')
    f = io.StringIO(text)
    channels = []
    groups = {}
    for ch in iter_m3u(f, filter_fn=filter_fn, drop_vod=drop_vod):
        idx = len(channels)
        channels.append(ch)
        g = ch.get('group') or 'Ungrouped'
        groups.setdefault(g, []).append(idx)
    log.debug(f"Index built: channels={len(channels)}, groups={len(groups)}")
    return {'channels': channels, 'groups': groups}
