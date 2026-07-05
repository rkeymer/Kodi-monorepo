import urllib.parse

def _join(base, path):
    base=(base or '').strip(); path=(path or '').strip()
    if not base:
        return path
    if base.endswith('/'):
        base=base[:-1]
    if not path.startswith('/'):
        path='/' + path
    return base + path

def build_m3u_url(base_url, m3u_path, username, password, m3u_type='m3u_plus', output='ts'):
    url=_join(base_url, m3u_path or '/get.php')
    q={'username': username or '', 'password': password or '', 'type': m3u_type or 'm3u_plus', 'output': output or 'ts'}
    return url + ('?' if '?' not in url else '&') + urllib.parse.urlencode(q)

def build_epg_url(base_url, epg_path, username, password):
    url=_join(base_url, epg_path or '/xmltv.php')
    q={'username': username or '', 'password': password or ''}
    return url + ('?' if '?' not in url else '&') + urllib.parse.urlencode(q)
