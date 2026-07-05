import os
import shutil
import xbmcvfs

from resources.lib import log

_OLD_ADDON_ID = 'plugin.video.whatsonnow'
_NEW_ADDON_ID = 'plugin.video.whatsonstreamer'

_FILES_TO_COPY = ['favourites.json', 'recent.json', 'playlist.m3u', 'epg.xml']


def migrate_from_whatsonnow_if_needed():
    """One-time import of WhatsOnNow's favourites/recent/local playlist+EPG cache
    into WhatsOnStreamer's own profile folder. They are separate addon IDs with
    separate addon_data directories, so nothing carries over automatically.
    """
    new_dir = xbmcvfs.translatePath(f'special://profile/addon_data/{_NEW_ADDON_ID}')
    marker = os.path.join(new_dir, 'livetv_migrated.flag')
    if os.path.exists(marker):
        return

    os.makedirs(new_dir, exist_ok=True)

    old_dir = xbmcvfs.translatePath(f'special://profile/addon_data/{_OLD_ADDON_ID}')
    if not os.path.isdir(old_dir):
        # Nothing to migrate (WhatsOnNow was never installed/used on this device) —
        # still write the marker so we don't keep re-checking on every open.
        _write_marker(marker)
        return

    copied = []
    for fname in _FILES_TO_COPY:
        src = os.path.join(old_dir, fname)
        dst = os.path.join(new_dir, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.copyfile(src, dst)
                copied.append(fname)
            except Exception as e:
                log.warn(f"Migration: failed to copy {fname}: {repr(e)}")

    if copied:
        log.info(f"Migrated from WhatsOnNow: {', '.join(copied)}")

    _write_marker(marker)


def _write_marker(marker_path: str):
    try:
        with open(marker_path, 'w', encoding='utf-8') as f:
            f.write('1')
    except Exception as e:
        log.warn(f"Migration: failed to write marker: {repr(e)}")
