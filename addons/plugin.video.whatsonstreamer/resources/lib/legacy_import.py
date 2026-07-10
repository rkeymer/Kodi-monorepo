import os
import xml.etree.ElementTree as ET

import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib import log

ADDON = xbmcaddon.Addon()

_WHATSONNOW_ID = 'plugin.video.whatsonnow'
_SIMKL_ID = 'plugin.video.simkl.watching'
_NEW_ADDON_ID = 'plugin.video.whatsonstreamer'

# old (WhatsOnNow) setting id -> new (WhatsOnStreamer) setting id.
# WhatsOnNow's own base_url/username/password fold into the shared iptv_* credentials
# now used by both Live TV and the IPTV VOD/series lookup.
_WHATSONNOW_MAP = {
    'playlist_source': 'livetv_playlist_source',
    'base_url': 'iptv_base_url',
    'username': 'iptv_username',
    'password': 'iptv_password',
    'm3u_path': 'livetv_m3u_path',
    'm3u_type': 'livetv_m3u_type',
    'output': 'livetv_output',
    'playlist_timeout': 'livetv_playlist_timeout',
    'playlist_retries': 'livetv_playlist_retries',
    'local_playlist_path': 'livetv_local_playlist_path',
    'epg_enabled': 'livetv_epg_enabled',
    'epg_source': 'livetv_epg_source',
    'epg_path': 'livetv_epg_path',
    'epg_timeout': 'livetv_epg_timeout',
    'epg_ttl': 'livetv_epg_ttl',
    'epg_use_conditional_get': 'livetv_epg_use_conditional_get',
    'local_epg_path': 'livetv_local_epg_path',
    'recent_max': 'livetv_recent_max',
    'on_now_pinned_recent': 'livetv_on_now_pinned_recent',
    'coming_up_hours': 'livetv_coming_up_hours',
    'include_groups': 'livetv_include_groups',
    'exclude_groups': 'livetv_exclude_groups',
    'include_keywords': 'livetv_include_keywords',
    'exclude_keywords': 'livetv_exclude_keywords',
    'include_regex': 'livetv_include_regex',
    'exclude_regex': 'livetv_exclude_regex',
    'auto_update_enabled': 'livetv_auto_update_enabled',
    'auto_update_mode': 'livetv_auto_update_mode',
    'auto_update_time': 'livetv_auto_update_time',
    'auto_update_interval_hours': 'livetv_auto_update_interval_hours',
    'auto_update_notify': 'livetv_auto_update_notify',
    'cache_ttl': 'livetv_cache_ttl',
    'page_size': 'livetv_page_size',
    'max_search_results': 'livetv_max_search_results',
    'use_local_cache_files': 'livetv_use_local_cache_files',
    'local_cache_base_path': 'livetv_local_cache_base_path',
    'local_cache_playlist_file': 'livetv_local_cache_playlist_file',
    'local_cache_epg_file': 'livetv_local_cache_epg_file',
}

# old (WhatsUpNext / simkl.watching) setting id -> new (WhatsOnStreamer) setting id.
# These addons share the same ids since WhatsOnStreamer's SIMKL/AllDebrid/TMDB
# categories were carried over from simkl.watching unchanged.
_SIMKL_MAP = {
    'client_id': 'client_id',
    'show_posters': 'show_posters',
    'access_token': 'access_token',
    'alldebrid_api_key': 'alldebrid_api_key',
    'local_media_path': 'local_media_path',
    'tmdb_api_key': 'tmdb_api_key',
    'airdate_source': 'airdate_source',
}

# Values for these ids may embed the old addon's own id as part of a
# special://profile/addon_data/<id>/... path and need rewriting to the new id.
_PATH_VALUE_IDS = {'local_playlist_path', 'local_epg_path'}


def _read_old_settings(addon_id: str) -> dict:
    """Read an old addon's persisted settings.xml as {id: text}. Empty dict if not found.

    Reads the file directly via special://profile rather than xbmcaddon.Addon(addon_id)
    so this still works after the old addon has been uninstalled, and identically on
    Android and Windows since special:// paths are resolved by Kodi either way.
    """
    path = xbmcvfs.translatePath(f'special://profile/addon_data/{addon_id}/settings.xml')
    if not os.path.exists(path):
        return {}
    try:
        tree = ET.parse(path)
    except Exception as e:
        log.warn(f"Import: failed to parse {addon_id} settings.xml: {repr(e)}")
        return {}
    out = {}
    for node in tree.getroot().iter('setting'):
        setting_id = node.get('id')
        if setting_id:
            out[setting_id] = (node.text or '').strip()
    return out


def _rewrite_old_addon_paths(value: str) -> str:
    return value.replace(_WHATSONNOW_ID, _NEW_ADDON_ID)


def _apply_mapped(old_values: dict, mapping: dict) -> list:
    applied = []
    for old_id, new_id in mapping.items():
        value = old_values.get(old_id, '')
        if value == '':
            continue
        if old_id in _PATH_VALUE_IDS:
            value = _rewrite_old_addon_paths(value)
        try:
            ADDON.setSetting(new_id, value)
            applied.append(new_id)
        except Exception as e:
            log.warn(f"Import: failed to set {new_id}: {repr(e)}")
    return applied


def _apply_debug_logging(sources: list) -> bool:
    """OR debug_logging across whichever old settings files declared it, so importing
    from both doesn't let the second source silently clobber a True from the first."""
    present = [s for s in sources if 'debug_logging' in s]
    if not present:
        return False
    combined = any(s.get('debug_logging') == 'true' for s in present)
    ADDON.setSetting('debug_logging', 'true' if combined else 'false')
    return True


def run() -> None:
    """Entry point for the Tools menu 'Import settings from WhatsOnNow / WhatsUpNext' action."""
    dialog = xbmcgui.Dialog()

    whatsonnow = _read_old_settings(_WHATSONNOW_ID)
    simkl = _read_old_settings(_SIMKL_ID)

    found = []
    if whatsonnow:
        found.append('WhatsOnNow')
    if simkl:
        found.append('WhatsUpNext (SIMKL)')

    if not found:
        dialog.ok(
            'WhatsOnStreamer - Import settings',
            'No previous WhatsOnNow or WhatsUpNext settings were found on this device.',
        )
        return

    if not dialog.yesno(
        'WhatsOnStreamer - Import settings',
        f"Found settings from: {', '.join(found)}.\n"
        'Import them into WhatsOnStreamer now? This overwrites any matching settings already set here.',
    ):
        return

    applied = []
    if whatsonnow:
        applied += _apply_mapped(whatsonnow, _WHATSONNOW_MAP)
    if simkl:
        applied += _apply_mapped(simkl, _SIMKL_MAP)
    if _apply_debug_logging([whatsonnow, simkl]):
        applied.append('debug_logging')

    log.info(f"Import: applied {len(applied)} settings from {', '.join(found)}")

    lines = [f"Imported {len(applied)} setting(s) from: {', '.join(found)}", '']
    lines += sorted(set(applied))
    dialog.textviewer('WhatsOnStreamer - Import settings', '\n'.join(lines))
