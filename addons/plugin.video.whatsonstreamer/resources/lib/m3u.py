import re

ATTR_RE = re.compile(r'(\w[\w-]*)="([^"]*)"')
VOD_URL_HINTS = ('/series/', '/movie/', '/vod/', '.mp4', '.mkv', '.avi', '.mov')
EPISODE_RE = re.compile(r'(?i)(S\d{1,2}\s*E\d{1,2}|S\d{1,2}E\d{1,2}|Season\s+\d+|Episode\s+\d+)')

def parse_extinf(line: str) -> dict:
    attrs = dict(ATTR_RE.findall(line))
    name = line.split(',', 1)[1].strip() if ',' in line else ''
    return {'name': name,
            'group': attrs.get('group-title') or attrs.get('group') or '',
            'logo': attrs.get('tvg-logo') or attrs.get('logo') or '',
            'tvg_id': attrs.get('tvg-id') or '',
            'tvg_name': attrs.get('tvg-name') or ''}

def _is_vod(url: str, name: str) -> bool:
    u = (url or '').lower()
    if any(h in u for h in VOD_URL_HINTS):
        return True
    return bool(EPISODE_RE.search(name or ''))

def iter_m3u(file_obj, filter_fn=None, drop_vod: bool = True):
    pending = None
    for raw in file_obj:
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#EXTM3U'):
            continue
        if line.startswith('#EXTINF'):
            pending = parse_extinf(line)
            continue
        if pending and (line.startswith('http://') or line.startswith('https://') or line.startswith('rtmp://') or line.startswith('rtsp://') or line.startswith('udp://')):
            url = line
            name = pending.get('name', '')
            if drop_vod and _is_vod(url, name):
                pending = None
                continue
            ch = dict(pending)
            ch['url'] = url
            pending = None
            if filter_fn is None or filter_fn(ch):
                yield ch
            continue
        if pending and not line.startswith('#'):
            pending = None
