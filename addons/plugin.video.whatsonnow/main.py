import sys
import time
import os
import urllib.parse
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import xbmcvfs

from resources.lib.filters import build_filter
from resources.lib.cache import is_fresh, load_json, save_json, ensure_dir
from resources.lib.playlist import build_m3u_url, build_epg_url
from resources.lib.playlist_fetch import fetch_url, parse_and_index
from resources.lib.http import download_to_file
from resources.lib.xmltv import extract_now_next_from_file, extract_schedule_from_file
from resources.lib import log

ADDON = xbmcaddon.Addon()
HANDLE = int(sys.argv[1])
MEDIA_PATH = os.path.join(ADDON.getAddonInfo('path'), 'resources', 'media')

PROFILE_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
CACHE_DIR = os.path.join(PROFILE_DIR, 'cache')
PLAYLIST_CACHE = os.path.join(CACHE_DIR, 'playlist_index.json')
EPG_CACHE = os.path.join(CACHE_DIR, 'epg_now_next.json')
EPG_META = os.path.join(CACHE_DIR, 'epg.meta.json')

ADDON_DATA_DIR = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.whatsonnow')
FAVS_PATH = os.path.join(ADDON_DATA_DIR, 'favourites.json')
RECENT_PATH = os.path.join(ADDON_DATA_DIR, 'recent.json')
STATE_PATH = os.path.join(ADDON_DATA_DIR, 'update_state.json')


def get_setting(setting_id: str, default: str = '') -> str:
    v = ADDON.getSetting(setting_id)
    return v if v != '' else default


def get_setting_int(setting_id: str, default: int) -> int:
    try:
        return int(float(get_setting(setting_id, str(default)) or default))
    except Exception:
        return default


def build_url(query: dict) -> str:
    return sys.argv[0] + '?' + urllib.parse.urlencode(query)


def _configured() -> bool:
    return bool(ADDON.getSetting('base_url') and ADDON.getSetting('username') and ADDON.getSetting('password'))


def _build_filter():
    return build_filter(
        include_groups=get_setting('include_groups'),
        exclude_groups=get_setting('exclude_groups'),
        include_keywords=get_setting('include_keywords'),
        exclude_keywords=get_setting('exclude_keywords'),
        include_regex=get_setting('include_regex'),
        exclude_regex=get_setting('exclude_regex'),
    )


def _fmt_hhmm(epoch: int) -> str:
    try:
        return time.strftime('%H:%M', time.localtime(epoch))
    except Exception:
        return ''


def _epg_line(label: str, prog: dict) -> str:
    if not prog:
        return f"{label}: (none)"
    title = prog.get('title') or ''
    start = prog.get('start') or 0
    stop = prog.get('stop') or 0
    return f"{label}: {title} ({_fmt_hhmm(start)}-{_fmt_hhmm(stop)})"


def _resolve_path(p: str) -> str:
    return xbmcvfs.translatePath(p) if p else ''


def _read_text_file(path: str) -> str:
    if not path or not xbmcvfs.exists(path):
        return ''
    f = xbmcvfs.File(path)
    try:
        return f.read()
    finally:
        f.close()


def _ensure_addon_data_dir():
    if not os.path.exists(ADDON_DATA_DIR):
        os.makedirs(ADDON_DATA_DIR, exist_ok=True)


def _load_list(path: str, default):
    _ensure_addon_data_dir()
    if not os.path.exists(path):
        return default
    try:
        import json
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_list(path: str, data):
    _ensure_addon_data_dir()
    try:
        import json
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log.warn(f"Failed to save {os.path.basename(path)}: {repr(e)}")


def load_favourites():
    return _load_list(FAVS_PATH, [])


def save_favourites(items):
    _save_list(FAVS_PATH, items)


def load_recent():
    return _load_list(RECENT_PATH, [])


def save_recent(items):
    _save_list(RECENT_PATH, items)


def clear_recent():
    save_recent([])


def clear_favourites():
    save_favourites([])


def _load_state():
    _ensure_addon_data_dir()
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        import json
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _fmt_ts(ts):
    try:
        ts=int(ts or 0)
    except Exception:
        ts=0
    if ts<=0:
        return 'n/a'
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))


def show_build_info():
    st=_load_state()
    lines=[
        'WhatsOnNow Build Info (v%s)' % ADDON.getAddonInfo('version'),
        '='*48,
        '',
        'Auto-update status',
        ' Enabled: %s' % get_setting('auto_update_enabled','false'),
        ' Time: %s' % get_setting('auto_update_time','02:00'),
        ' Notify: %s' % get_setting('auto_update_notify','true'),
        '',
        'Timeout defaults',
        ' Playlist timeout: %s' % get_setting('playlist_timeout','600'),
        ' EPG timeout: %s' % get_setting('epg_timeout','600'),
        '',
        'Last run timestamps',
        ' Last auto update: %s' % _fmt_ts(st.get('last_auto_update',0)),
        ' Last manual update: %s' % _fmt_ts(st.get('last_manual_update',0)),
        ' Last service date: %s' % st.get('last_service_update_date','n/a'),
    ]
    xbmcgui.Dialog().textviewer('WhatsOnNow - Build info', "\n".join(lines))


def channel_to_entry(ch: dict) -> dict:
    return {
        'name': ch.get('name') or 'Channel',
        'url': ch.get('url') or '',
        'tvg_id': ch.get('tvg_id') or '',
        'logo': ch.get('logo') or '',
        'group': ch.get('group') or ''
    }


def is_favourite(url: str) -> bool:
    url = url or ''
    for it in load_favourites():
        if (it.get('url') or '') == url:
            return True
    return False


def add_favourite(entry: dict):
    url = entry.get('url') or ''
    if not url:
        return
    favs = [f for f in load_favourites() if (f.get('url') or '') != url]
    entry = dict(entry)
    entry['added'] = int(time.time())
    favs.insert(0, entry)
    save_favourites(favs)


def remove_favourite(url: str):
    url = url or ''
    favs = [f for f in load_favourites() if (f.get('url') or '') != url]
    save_favourites(favs)


def update_recent_from_entry(entry: dict):
    url = entry.get('url') or ''
    if not url:
        return
    max_len = get_setting_int('recent_max', 50)
    rec = [r for r in load_recent() if (r.get('url') or '') != url]
    entry = dict(entry)
    entry['last_played'] = int(time.time())
    rec.insert(0, entry)
    save_recent(rec[:max_len])


def _add_separator(title: str):
    li = xbmcgui.ListItem(f'[B][COLOR gold]{title}[/COLOR][/B]')
    li.setProperty('IsPlayable', 'false')
    li.setArt({'icon': 'DefaultFolder.png', 'thumb': ''})
    xbmcplugin.addDirectoryItem(HANDLE, '', li, False)


def _add_spacer():
    li = xbmcgui.ListItem(' ')
    li.setProperty('IsPlayable', 'false')
    li.setArt({'icon': 'DefaultAddonNone.png', 'thumb': ''})
    xbmcplugin.addDirectoryItem(HANDLE, '', li, False)


def load_playlist_index(force: bool = False) -> dict:
    ttl_minutes = get_setting_int('cache_ttl', 10)
    ttl_seconds = ttl_minutes * 60
    ensure_dir(CACHE_DIR)

    if not force and is_fresh(PLAYLIST_CACHE, ttl_seconds):
        try:
            return load_json(PLAYLIST_CACHE)
        except Exception:
            pass

    drop_vod = (get_setting('drop_vod', 'true').lower() == 'true')
    filter_fn = _build_filter()

    playlist_source = get_setting('playlist_source', '0')
    local_playlist_path = get_setting('local_playlist_path', '')

    base_url = get_setting('base_url')
    username = get_setting('username')
    password = get_setting('password')
    m3u_path = get_setting('m3u_path', '/get.php')
    m3u_type = get_setting('m3u_type', 'm3u_plus')
    output = get_setting('output', 'ts')

    epg_url = None
    if get_setting('epg_enabled', 'true').lower() == 'true':
        epg_path = get_setting('epg_path', '/xmltv.php')
        if base_url and username and password:
            epg_url = build_epg_url(base_url, epg_path, username, password)

    if playlist_source == '1':
        log.info('Playlist source: Local file')
        text = _read_text_file(local_playlist_path)
        if not text:
            xbmcgui.Dialog().ok('WhatsOnNow', 'Local playlist file not found or empty. Check Local playlist path in Settings.')
            return {'channels': [], 'groups': {}, '_meta': {'epg_url': epg_url}}
        data = text.encode('utf-8', 'replace')
        index = parse_and_index(data, filter_fn=filter_fn, drop_vod=drop_vod)
        index['_meta'] = {'epg_url': epg_url}
        save_json(PLAYLIST_CACHE, index)
        return index

    timeout_s = get_setting_int('playlist_timeout', 600)
    retries = get_setting_int('playlist_retries', 3)

    if not base_url or not username or not password:
        xbmcgui.Dialog().ok(ADDON.getAddonInfo('name'), 'Please set Base URL, Username, and Password in Settings.')
        return {'channels': [], 'groups': {}, '_meta': {'epg_url': epg_url}}

    m3u_url = build_m3u_url(base_url, m3u_path, username, password, m3u_type, output)
    log.info(f"Playlist source: URL with auto-fallback. timeout={timeout_s}s retries={retries} drop_vod={drop_vod}")

    data = None
    try:
        data = fetch_url(m3u_url, timeout=timeout_s, retries=retries)
    except Exception as e:
        log.warn(f"Playlist URL fetch failed: {repr(e)}")

    if not data:
        text = _read_text_file(local_playlist_path)
        if text:
            log.warn('Falling back to local playlist file')
            data = text.encode('utf-8', 'replace')
        else:
            msg = 'Playlist download failed and no local playlist file is available.' + '\n\n' + 'Tip: switch Playlist source to Local file once you have a cached playlist.'
            xbmcgui.Dialog().ok('WhatsOnNow', msg)
            return {'channels': [], 'groups': {}, '_meta': {'epg_url': epg_url}}

    # Update local cache
    if data and local_playlist_path:
        try:
            real_path = _resolve_path(local_playlist_path)
            parent = os.path.dirname(real_path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(real_path, 'wb') as out:
                out.write(data)
        except Exception as e:
            log.warn(f"Failed to write local playlist cache: {repr(e)}")

    index = parse_and_index(data, filter_fn=filter_fn, drop_vod=drop_vod)
    index['_meta'] = {'epg_url': epg_url}
    save_json(PLAYLIST_CACHE, index)

    tvg_present = sum(1 for ch in index.get('channels', []) if (ch.get('tvg_id') or '').strip())
    log.info(f"Playlist parsed: channels={len(index.get('channels', []))}, groups={len(index.get('groups', {}))}, tvg_ids={tvg_present}")
    return index


def load_epg_now_next(force: bool, playlist_index: dict) -> dict:
    if get_setting('epg_enabled', 'true').lower() != 'true':
        return {}

    ttl_minutes = get_setting_int('epg_ttl', 180)
    ttl_seconds = ttl_minutes * 60

    if not force and is_fresh(EPG_CACHE, ttl_seconds):
        try:
            return load_json(EPG_CACHE)
        except Exception:
            pass

    wanted = set()
    for ch in playlist_index.get('channels', []):
        cid = (ch.get('tvg_id') or '').strip()
        if cid:
            wanted.add(cid)

    if not wanted:
        log.warn('No tvg-id values found in playlist; cannot map EPG.')
        return {}

    epg_source = get_setting('epg_source', '0')
    local_epg_path = get_setting('local_epg_path', '')

    if epg_source == '1':
        log.info('EPG source: Local file')
        if not local_epg_path or not xbmcvfs.exists(local_epg_path):
            return {}
        epg_map = extract_now_next_from_file(local_epg_path, wanted, now_epoch=int(time.time()))
        save_json(EPG_CACHE, epg_map)
        return epg_map

    epg_url = (playlist_index.get('_meta') or {}).get('epg_url')
    if not epg_url:
        return {}

    use_conditional = (get_setting('epg_use_conditional_get', 'true').lower() == 'true')
    dest = local_epg_path or 'special://profile/addon_data/plugin.video.whatsonnow/epg.xml'

    try:
        download_to_file(epg_url, dest, meta_path=EPG_META, use_conditional=use_conditional, timeout=90)
    except Exception as e:
        log.warn(f"EPG URL download failed: {repr(e)}")
        if local_epg_path and xbmcvfs.exists(local_epg_path):
            epg_map = extract_now_next_from_file(local_epg_path, wanted, now_epoch=int(time.time()))
            save_json(EPG_CACHE, epg_map)
            return epg_map
        return {}

    if xbmcvfs.exists(dest):
        epg_map = extract_now_next_from_file(dest, wanted, now_epoch=int(time.time()))
        save_json(EPG_CACHE, epg_map)
        return epg_map

    return {}


def _add_pager(start: int, page_size: int, total: int, base_query: dict):
    if start > 0:
        prev_start = max(0, start - page_size)
        xbmcplugin.addDirectoryItem(HANDLE, build_url({**base_query, 'start': str(prev_start)}), xbmcgui.ListItem(f"<< Previous ({prev_start+1}-{min(prev_start+page_size, total)})"), True)
    if start + page_size < total:
        next_start = start + page_size
        xbmcplugin.addDirectoryItem(HANDLE, build_url({**base_query, 'start': str(next_start)}), xbmcgui.ListItem(f"Next ({next_start+1}-{min(next_start+page_size, total)}) >>"), True)


def _add_channel_item(ch: dict, index_i: int = None, epg_map: dict = None, url_override: str = None):
    name = ch.get('name') or 'Channel'
    url = url_override or ch.get('url') or ''

    display_name = name
    plot = None

    if epg_map is not None:
        cid = (ch.get('tvg_id') or '').strip()
        slot = epg_map.get(cid) if cid else None
        if slot:
            nowp = slot.get('now')
            if nowp and nowp.get('title'):
                display_name = f"{name} - {nowp.get('title')}"
            plot = _epg_line('Now', slot.get('now')) + "\n" + _epg_line('Next', slot.get('next'))

    li = xbmcgui.ListItem(display_name)
    li.setProperty('IsPlayable', 'true')

    logo = ch.get('logo')
    if logo:
        li.setArt({'thumb': logo, 'icon': logo})

    if plot:
        # Deprecated warning in Kodi 21 - acceptable for now
        li.setInfo('video', {'plot': plot})

    # Context menu for favourites
    cm = []
    if is_favourite(url):
        cm.append(('Remove from favourites', f"RunPlugin({build_url({'action':'fav_remove_url','u':url})})"))
    else:
        if index_i is not None:
            cm.append(('Add to favourites', f"RunPlugin({build_url({'action':'fav_add','i':str(index_i)})})"))
        else:
            cm.append(('Add to favourites', f"RunPlugin({build_url({'action':'fav_add_url','u':url,'n':name})})"))

    li.addContextMenuItems(cm, replaceItems=True)

    # Playback route
    if index_i is not None:
        play_url = build_url({'action': 'play', 'i': str(index_i)})
    else:
        play_url = build_url({'action': 'play_url', 'u': url, 'n': name})

    xbmcplugin.addDirectoryItem(HANDLE, play_url, li, False)


_INDENT = ' ' * 6  # non-breaking spaces — Kodi strips regular leading spaces

def _add_programme_item(channel_play_url: str, programme_title: str, start_epoch: int, stop_epoch: int, logo: str = ''):
    st = _fmt_hhmm(start_epoch)
    sp = _fmt_hhmm(stop_epoch)
    label = f"{_INDENT}{programme_title}  {st}–{sp}"
    li = xbmcgui.ListItem(label)
    li.setProperty('IsPlayable', 'true')
    if logo:
        li.setArt({'thumb': logo, 'icon': logo})
    li.setInfo('video', {'plot': label})
    xbmcplugin.addDirectoryItem(HANDLE, channel_play_url, li, False)


def list_tools():
    items = [
        ('Build info', {'action': 'build_info'}, False),
        ('Run auto-update now (service)', {'action': 'svc_update'}, False),
    ]
    for label, query, is_folder in items:
        xbmcplugin.addDirectoryItem(HANDLE, build_url(query), xbmcgui.ListItem(label), is_folder)
    xbmcplugin.endOfDirectory(HANDLE)


def list_root():
    xbmcplugin.setPluginCategory(HANDLE, 'WhatsOnNow')
    items = [
        ('On Now',                {'action': 'on_now'},             'on_now.png'),
        ('Coming Up (Next hours)',{'action': 'coming_up'},          'coming_up.png'),
        ('Favourites',            {'action': 'favs'},               'favourites.png'),
        ('Recently Watched',      {'action': 'recent'},             'recently_watched.png'),
        ('Groups',                {'action': 'groups'},             'groups.png'),
        ('Search',                {'action': 'search'},             'search.png'),
        ('All Channels (paged)',  {'action': 'all', 'start': '0'},  'all_channels.png'),
        ('Tools / Diagnostics',   {'action': 'tools'},              'tools_diagnostics.png'),
    ]
    for label, query, icon_file in items:
        li = xbmcgui.ListItem(label)
        icon = os.path.join(MEDIA_PATH, icon_file)
        li.setArt({'icon': icon, 'thumb': icon})
        xbmcplugin.addDirectoryItem(HANDLE, build_url(query), li, True)
    xbmcplugin.endOfDirectory(HANDLE)


def list_favourites():
    favs = load_favourites()
    li = xbmcgui.ListItem('Clear favourites')
    li.setProperty('IsPlayable', 'false')
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action':'fav_clear'}), li, False)
    _add_spacer()
    for f in favs:
        ch = {'name': f.get('name'), 'url': f.get('url'), 'tvg_id': f.get('tvg_id',''), 'logo': f.get('logo',''), 'group': f.get('group','')}
        _add_channel_item(ch, index_i=None, epg_map=None, url_override=f.get('url'))
    xbmcplugin.endOfDirectory(HANDLE)


def list_recent():
    rec = load_recent()
    li = xbmcgui.ListItem('Clear recently watched')
    li.setProperty('IsPlayable', 'false')
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action':'recent_clear'}), li, False)
    _add_spacer()
    for r in rec:
        ch = {'name': r.get('name'), 'url': r.get('url'), 'tvg_id': r.get('tvg_id',''), 'logo': r.get('logo',''), 'group': r.get('group','')}
        _add_channel_item(ch, index_i=None, epg_map=None, url_override=r.get('url'))
    xbmcplugin.endOfDirectory(HANDLE)


def list_groups():
    idx = load_playlist_index(force=False)
    groups = sorted(idx.get('groups', {}).keys(), key=lambda s: s.lower())
    for g in groups:
        count = len(idx['groups'][g])
        xbmcplugin.addDirectoryItem(HANDLE, build_url({'action': 'group', 'name': g, 'start': '0'}), xbmcgui.ListItem(f"{g} ({count})"), True)
    xbmcplugin.endOfDirectory(HANDLE)


def list_group(name: str, start: int):
    idx = load_playlist_index(force=False)
    epg = load_epg_now_next(force=False, playlist_index=idx)
    indices = idx.get('groups', {}).get(name, [])
    total = len(indices)
    page_size = get_setting_int('page_size', 200)
    _add_pager(start, page_size, total, {'action': 'group', 'name': name})
    channels = idx.get('channels', [])
    for ch_i in indices[start:start+page_size]:
        _add_channel_item(channels[ch_i], index_i=ch_i, epg_map=epg)
    _add_pager(start, page_size, total, {'action': 'group', 'name': name})
    xbmcplugin.endOfDirectory(HANDLE)


def list_all_channels(start: int):
    idx = load_playlist_index(force=False)
    epg = load_epg_now_next(force=False, playlist_index=idx)
    channels = idx.get('channels', [])
    total = len(channels)
    page_size = get_setting_int('page_size', 200)
    _add_pager(start, page_size, total, {'action': 'all'})
    for i in range(start, min(start + page_size, total)):
        _add_channel_item(channels[i], index_i=i, epg_map=epg)
    _add_pager(start, page_size, total, {'action': 'all'})
    xbmcplugin.endOfDirectory(HANDLE)


def list_on_now():
    idx = load_playlist_index(force=False)
    epg = load_epg_now_next(force=False, playlist_index=idx)
    channels = idx.get('channels', [])

    by_url = {}
    for i, ch in enumerate(channels):
        u = ch.get('url') or ''
        if u:
            by_url[u] = (i, ch)

    pinned_n = get_setting_int('on_now_pinned_recent', 8)
    recent = load_recent()[:pinned_n]
    pinned_urls = set()

    if recent:
        _add_separator('— Recently Watched —')
    for r in recent:
        ru = r.get('url') or ''
        pinned_urls.add(ru)
        if ru in by_url:
            i, ch = by_url[ru]
            _add_channel_item(ch, index_i=i, epg_map=epg)
        else:
            ch = {'name': r.get('name'), 'url': ru, 'tvg_id': r.get('tvg_id',''), 'logo': r.get('logo',''), 'group': r.get('group','')}
            _add_channel_item(ch, index_i=None, epg_map=epg, url_override=ru)

    _add_spacer()
    _coming_up_li = xbmcgui.ListItem('[B][COLOR gold]Coming Up (Next hours)[/COLOR][/B]')
    _coming_up_li.setArt({'icon': os.path.join(MEDIA_PATH, 'coming_up.png'), 'thumb': os.path.join(MEDIA_PATH, 'coming_up.png')})
    xbmcplugin.addDirectoryItem(HANDLE, build_url({'action':'coming_up'}), _coming_up_li, True)
    _add_spacer()

    _add_separator('— On Now —')
    for i, ch in enumerate(channels):
        u = ch.get('url') or ''
        if u and u in pinned_urls:
            continue
        cid = (ch.get('tvg_id') or '').strip()
        slot = epg.get(cid) if cid else None
        if slot and slot.get('now'):
            _add_channel_item(ch, index_i=i, epg_map=epg)

    xbmcplugin.endOfDirectory(HANDLE)


def list_coming_up():
    idx = load_playlist_index(force=False)
    channels = idx.get('channels', [])

    by_url = {}
    for i, ch in enumerate(channels):
        u = ch.get('url') or ''
        if u:
            by_url[u] = (i, ch)

    pinned_n = get_setting_int('on_now_pinned_recent', 8)
    recent = load_recent()[:pinned_n]

    pinned = []
    wanted = set()

    for r in recent:
        ru = r.get('url') or ''
        if ru in by_url:
            i, ch = by_url[ru]
            pinned.append({'i': i, 'name': ch.get('name') or 'Channel', 'url': ru, 'tvg_id': (ch.get('tvg_id') or '').strip(), 'logo': ch.get('logo') or ''})
        else:
            pinned.append({'i': None, 'name': r.get('name') or 'Channel', 'url': ru, 'tvg_id': (r.get('tvg_id') or '').strip(), 'logo': r.get('logo') or ''})
        if pinned[-1]['tvg_id']:
            wanted.add(pinned[-1]['tvg_id'])

    if not wanted:
        xbmcgui.Dialog().ok('WhatsOnNow', 'Coming Up requires tvg-id values for the pinned channels (playlist tvg-id -> XMLTV channel mapping).')
        xbmcplugin.endOfDirectory(HANDLE)
        return

    epg_path = get_setting('local_epg_path', '') or 'special://profile/addon_data/plugin.video.whatsonnow/epg.xml'
    if not xbmcvfs.exists(epg_path):
        xbmcgui.Dialog().ok('WhatsOnNow', 'EPG data not available yet. Open "On Now" first to fetch the EPG, then try again.')
        xbmcplugin.endOfDirectory(HANDLE)
        return

    hours = get_setting_int('coming_up_hours', 5)
    now = int(time.time())
    end_epoch = now + max(1, hours) * 3600

    schedules = extract_schedule_from_file(epg_path, wanted, now, end_epoch, max_per_channel=50)

    for ch in pinned:
        header = xbmcgui.ListItem(f"[B]{ch['name']}[/B]")
        header.setProperty('IsPlayable', 'false')
        if ch['logo']:
            header.setArt({'thumb': ch['logo'], 'icon': ch['logo']})
        else:
            header.setArt({'icon': 'DefaultFolder.png', 'thumb': ''})
        xbmcplugin.addDirectoryItem(HANDLE, '', header, False)

        if ch['i'] is not None:
            play_url = build_url({'action':'play','i':str(ch['i'])})
        else:
            play_url = build_url({'action':'play_url','u':ch['url'],'n':ch['name']})

        progs = schedules.get(ch['tvg_id'], [])
        if not progs:
            li = xbmcgui.ListItem(f'{_INDENT}(No upcoming programmes in this window)')
            li.setProperty('IsPlayable', 'false')
            xbmcplugin.addDirectoryItem(HANDLE, '', li, False)
            _add_spacer()
            continue

        for p in progs:
            title = p.get('title') or '(untitled)'
            _add_programme_item(play_url, title, int(p.get('start',0)), int(p.get('stop',0)), logo=ch['logo'])

        _add_spacer()

    xbmcplugin.endOfDirectory(HANDLE)


def do_search():
    idx = load_playlist_index(force=False)
    epg = load_epg_now_next(force=False, playlist_index=idx)
    channels = idx.get('channels', [])

    max_results = get_setting_int('max_search_results', 200)
    q = xbmcgui.Dialog().input('Search channels', type=xbmcgui.INPUT_ALPHANUM)
    if not q:
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    ql = q.lower().strip()
    hits = 0

    for i, ch in enumerate(channels):
        if ql in (ch.get('name') or '').lower():
            _add_channel_item(ch, index_i=i, epg_map=epg)
            hits += 1
            if hits >= max_results:
                break

    xbmcplugin.endOfDirectory(HANDLE)


def play_channel_by_index(i: str):
    idx = load_playlist_index(force=False)
    channels = idx.get('channels', [])

    try:
        ii = int(i)
    except Exception:
        return

    if ii < 0 or ii >= len(channels):
        return

    ch = channels[ii]
    url = ch.get('url')
    if not url:
        return

    update_recent_from_entry(channel_to_entry(ch))

    li = xbmcgui.ListItem(path=url)
    li.setProperty('IsPlayable', 'true')
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def play_channel_by_url(url: str, name: str = ''):
    url = url or ''
    if not url:
        return

    update_recent_from_entry({'name': name or url, 'url': url, 'tvg_id': '', 'logo': '', 'group': ''})

    li = xbmcgui.ListItem(path=url)
    li.setProperty('IsPlayable', 'true')
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def play_m3u(url: str = '', name: str = ''):
    local_playlist_path = get_setting('local_playlist_path', '')

    if local_playlist_path:
        try:
            if xbmcvfs.exists(local_playlist_path):
                st = xbmcvfs.Stat(local_playlist_path)
                if st.st_size() > 0:
                    li = xbmcgui.ListItem(path=local_playlist_path)
                    li.setProperty('IsPlayable', 'true')
                    xbmcplugin.setResolvedUrl(HANDLE, True, li)
                    return
                log.error(f"Play M3U failed: local playlist file is empty at {local_playlist_path}")
            else:
                log.error(f"Play M3U failed: local playlist file not found at {local_playlist_path}")
        except Exception as e:
            log.error(f"Play M3U failed reading local playlist file {local_playlist_path}: {repr(e)}")
    else:
        log.error('Play M3U failed: no local playlist path configured')

    if _configured():
        base_url = get_setting('base_url')
        username = get_setting('username')
        password = get_setting('password')
        m3u_path = get_setting('m3u_path', '/get.php')
        m3u_type = get_setting('m3u_type', 'm3u_plus')
        output = get_setting('output', 'ts')

        try:
            m3u_url = build_m3u_url(base_url, m3u_path, username, password, m3u_type, output)
            log.info(f"Play M3U fallback to remote playlist URL: {m3u_url}")
            li = xbmcgui.ListItem(path=m3u_url)
            li.setProperty('IsPlayable', 'true')
            xbmcplugin.setResolvedUrl(HANDLE, True, li)
            return
        except Exception as e:
            log.error(f"Play M3U failed opening remote playlist URL: {repr(e)}")

    xbmcgui.Dialog().ok('WhatsOnNow', 'Unable to play M3U playlist. Check kodi.log for details.')


def fav_add_index(i: str):
    idx = load_playlist_index(force=False)
    channels = idx.get('channels', [])

    try:
        ii = int(i)
    except Exception:
        return

    if ii < 0 or ii >= len(channels):
        return

    add_favourite(channel_to_entry(channels[ii]))
    xbmcgui.Dialog().notification('WhatsOnNow', 'Added to favourites', xbmcgui.NOTIFICATION_INFO, 1500)


def fav_add_url(url: str, name: str = ''):
    add_favourite({'name': name or 'Channel', 'url': url, 'tvg_id': '', 'logo': '', 'group': ''})
    xbmcgui.Dialog().notification('WhatsOnNow', 'Added to favourites', xbmcgui.NOTIFICATION_INFO, 1500)


def fav_remove_url(url: str):
    remove_favourite(url)
    xbmcgui.Dialog().notification('WhatsOnNow', 'Removed from favourites', xbmcgui.NOTIFICATION_INFO, 1500)


def test_local_files():
    pl = get_setting('local_playlist_path', '')
    ep = get_setting('local_epg_path', '')

    lines = []

    if pl and xbmcvfs.exists(pl):
        st = xbmcvfs.Stat(pl)
        hdr = (_read_text_file(pl).splitlines()[:1] or [''])[0]
        lines += ['Playlist: OK', f" Path: {pl}", f" Size: {st.st_size()} bytes", f" First line: {hdr}"]
    else:
        lines += ['Playlist: MISSING', f" Path: {pl}"]

    lines.append('')

    if ep and xbmcvfs.exists(ep):
        st = xbmcvfs.Stat(ep)
        hdr = (_read_text_file(ep).splitlines()[:1] or [''])[0]
        lines += ['EPG: OK', f" Path: {ep}", f" Size: {st.st_size()} bytes", f" First line: {hdr}"]
    else:
        lines += ['EPG: MISSING', f" Path: {ep}"]

    xbmcgui.Dialog().ok('WhatsOnNow - Local files', '\n'.join(lines))


def test_connection():
    idx = load_playlist_index(force=True)
    channels = idx.get('channels', [])
    tvg_present = sum(1 for ch in channels if (ch.get('tvg_id') or '').strip())
    xbmcgui.Dialog().ok('WhatsOnNow - Test', f"Channels (after filters): {len(channels)}\nTVG-IDs present: {tvg_present}")


def router(paramstring: str):
    params = dict(urllib.parse.parse_qsl(paramstring))
    action = params.get('action')

    if action is None:
        if get_setting('playlist_source', '0') == '0' and not _configured():
            ADDON.openSettings()
        list_root()
        return

    if action == 'on_now':
        list_on_now()
    elif action == 'coming_up':
        list_coming_up()
    elif action == 'favs':
        list_favourites()
    elif action == 'recent':
        list_recent()
    elif action == 'recent_clear':
        clear_recent(); xbmc.executebuiltin('Container.Refresh')
    elif action == 'fav_clear':
        clear_favourites(); xbmc.executebuiltin('Container.Refresh')
    elif action == 'fav_add':
        fav_add_index(params.get('i', '')); xbmc.executebuiltin('Container.Refresh')
    elif action == 'fav_add_url':
        fav_add_url(params.get('u', ''), params.get('n','')); xbmc.executebuiltin('Container.Refresh')
    elif action == 'fav_remove_url':
        fav_remove_url(params.get('u', '')); xbmc.executebuiltin('Container.Refresh')
    elif action == 'groups':
        list_groups()
    elif action == 'group':
        list_group(params.get('name',''), int(params.get('start','0') or '0'))
    elif action == 'all':
        list_all_channels(int(params.get('start','0') or '0'))
    elif action == 'search':
        do_search()
    elif action == 'play':
        play_channel_by_index(params.get('i',''))
    elif action == 'play_url':
        play_channel_by_url(params.get('u',''), params.get('n',''))
    elif action == 'play_m3u':
        play_m3u(params.get('u',''), params.get('n',''))
    elif action == 'refresh':
        load_playlist_index(force=True); xbmc.executebuiltin('Container.Refresh')
    elif action == 'refresh_epg':
        idx = load_playlist_index(force=False)
        load_epg_now_next(force=True, playlist_index=idx)
        xbmc.executebuiltin('Container.Refresh')
    elif action == 'tools':
        list_tools()
    elif action == 'build_info':
        show_build_info()
    elif action == 'svc_update':
        xbmc.executebuiltin('RunScript(special://home/addons/plugin.video.whatsonnow/service.py,manual)')
    elif action == 'settings':
        ADDON.openSettings()
    elif action == 'test_local':
        test_local_files()
    elif action == 'test':
        test_connection()
    else:
        # Always end directory to avoid GetDirectory errors
        xbmcplugin.endOfDirectory(HANDLE)


if __name__ == '__main__':
    router(sys.argv[2][1:])
