import json
import os

import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib import log

ADDON = xbmcaddon.Addon()
PROFILE_DIR = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
STORE_PATH = os.path.join(PROFILE_DIR, 'iptv_profiles.json')


def _ensure_dir():
    if not xbmcvfs.exists(PROFILE_DIR):
        xbmcvfs.mkdirs(PROFILE_DIR)


def _migrate_from_settings() -> dict:
    """First run: the addon has always had exactly one static IPTV account in
    iptv_base_url/iptv_username/iptv_password - carry that forward as
    'Profile 1' rather than starting the user from a blank slate."""
    profile = {
        'name': 'Profile 1',
        'base_url': ADDON.getSetting('iptv_base_url'),
        'username': ADDON.getSetting('iptv_username'),
        'password': ADDON.getSetting('iptv_password'),
    }
    data = {'profiles': [profile], 'active': 0}
    _save(data)
    log.info("IPTV profiles: migrated existing settings into 'Profile 1'")
    return data


def _load() -> dict:
    _ensure_dir()
    if not xbmcvfs.exists(STORE_PATH):
        return _migrate_from_settings()
    try:
        f = xbmcvfs.File(STORE_PATH, 'r')
        raw = f.read()
        f.close()
        data = json.loads(raw) if raw else {}
        if not data.get('profiles'):
            return _migrate_from_settings()
        return data
    except Exception as e:
        log.warn(f"IPTV profiles: failed to read store, re-migrating: {repr(e)}")
        return _migrate_from_settings()


def _save(data: dict):
    _ensure_dir()
    f = xbmcvfs.File(STORE_PATH, 'w')
    f.write(json.dumps(data, ensure_ascii=False))
    f.close()


def _apply(data: dict, index: int):
    """Write the chosen profile's credentials into the live iptv_base_url/
    iptv_username/iptv_password settings - every consumer (iptv_api.py,
    livetv.py, service.py) reads those three directly, so this is the only
    integration point needed; nothing else has to know profiles exist."""
    p = data['profiles'][index]
    ADDON.setSetting('iptv_base_url', p.get('base_url', ''))
    ADDON.setSetting('iptv_username', p.get('username', ''))
    ADDON.setSetting('iptv_password', p.get('password', ''))
    data['active'] = index
    _save(data)
    log.info(f"IPTV profiles: switched active profile to '{p.get('name')}'")


def _prompt_profile_fields(existing: dict = None):
    """Kodi's Dialog().input() returns '' both when the user backspaces a
    field empty AND when they just press Cancel/Back on it - there's no way
    to tell those apart from the return value alone. When editing (existing
    is set), treat an empty result as "leave this field alone" rather than
    blanking it, otherwise cancelling out of the username/password prompt
    silently wipes a working credential."""
    existing = existing or {}
    dialog = xbmcgui.Dialog()
    name = dialog.input('Profile name', existing.get('name', ''))
    if not name or not name.strip():
        return None
    base_url = dialog.input('IPTV provider base URL (no path)', existing.get('base_url', ''))
    username = dialog.input('IPTV username', existing.get('username', ''))
    password = dialog.input('IPTV password', existing.get('password', ''))
    return {
        'name': name.strip(),
        'base_url': (base_url or '').strip() or existing.get('base_url', ''),
        'username': (username or '').strip() or existing.get('username', ''),
        'password': (password or '').strip() or existing.get('password', ''),
    }


def manage():
    """Entry point for the 'Live TV: Manage IPTV Profiles' Tools menu item."""
    data = _load()
    dialog = xbmcgui.Dialog()

    while True:
        profiles = data.get('profiles') or []
        active = data.get('active', 0)
        options = [
            p['name'] + ('  (active)' if i == active else '')
            for i, p in enumerate(profiles)
        ]
        options.append('+ Add new profile')

        choice = dialog.select('IPTV Profiles', options)
        if choice < 0:
            return

        if choice == len(profiles):
            new_profile = _prompt_profile_fields()
            if not new_profile:
                continue
            profiles.append(new_profile)
            data['profiles'] = profiles
            _save(data)
            if dialog.yesno('WhatsOnStreamer', f"Switch to '{new_profile['name']}' now?"):
                _apply(data, len(profiles) - 1)
                dialog.notification('WhatsOnStreamer', f"Now using '{new_profile['name']}'", xbmcgui.NOTIFICATION_INFO, 3000)
            continue

        p = profiles[choice]
        sub = dialog.select(p['name'], ['Use this profile', 'Edit', 'Delete'])
        if sub == 0:
            _apply(data, choice)
            dialog.notification('WhatsOnStreamer', f"Now using '{p['name']}'", xbmcgui.NOTIFICATION_INFO, 3000)
        elif sub == 1:
            edited = _prompt_profile_fields(p)
            if edited:
                profiles[choice] = edited
                data['profiles'] = profiles
                _save(data)
                if choice == active:
                    _apply(data, choice)
        elif sub == 2:
            if len(profiles) <= 1:
                dialog.notification('WhatsOnStreamer', "Can't delete the only profile", xbmcgui.NOTIFICATION_WARNING, 3000)
            elif dialog.yesno('WhatsOnStreamer', f"Delete profile '{p['name']}'?"):
                was_active = (choice == active)
                del profiles[choice]
                data['profiles'] = profiles
                if was_active:
                    _apply(data, 0)
                elif active > choice:
                    data['active'] = active - 1
                    _save(data)
                else:
                    _save(data)
        # else: dialog dismissed (sub == -1) - loop back to the profile list
