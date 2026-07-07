import os
import xml.etree.ElementTree as ET

import xbmcaddon
import xbmcgui

from resources.lib import log

ADDON = xbmcaddon.Addon()

# Two levels up from resources/lib/ is the addon root, same as resources/settings.xml.
# Derived from __file__ rather than ADDON.getAddonInfo('path') for the same reason
# service.py does: that call can come back empty depending on invocation context.
_DEFINITIONS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'settings.xml')


def _iter_defaults():
    """Yield (setting_id, default_value) for every setting declared in resources/settings.xml."""
    tree = ET.parse(_DEFINITIONS_PATH)
    for node in tree.getroot().iter('setting'):
        setting_id = node.get('id')
        default = node.get('default')
        if setting_id is not None and default is not None:
            yield setting_id, default


def reset_to_defaults() -> int:
    """Reset every WhatsOnStreamer setting back to its declared default. Returns the count reset."""
    count = 0
    for setting_id, default in _iter_defaults():
        try:
            ADDON.setSetting(setting_id, default)
            count += 1
        except Exception as e:
            log.warn(f"Reset: failed to reset {setting_id}: {repr(e)}")
    return count


def confirm_and_reset() -> None:
    dialog = xbmcgui.Dialog()
    if not dialog.yesno(
        'WhatsOnStreamer - Reset settings',
        'Reset ALL settings to their defaults?\n'
        'This clears API keys, IPTV credentials, and all Live TV configuration.',
    ):
        return
    count = reset_to_defaults()
    log.info(f"Reset: {count} settings restored to defaults")
    dialog.notification('WhatsOnStreamer', f'{count} settings reset to defaults', xbmcgui.NOTIFICATION_INFO, 4000)
