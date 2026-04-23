import xbmc
import xbmcaddon

ADDON_ID = 'plugin.video.whatsonnow'
ADDON = xbmcaddon.Addon(ADDON_ID)

def _enabled():
    try:
        return ADDON.getSetting('debug_logging').lower() == 'true'
    except Exception:
        return False

def info(msg: str):
    xbmc.log(f"[{ADDON_ID}] {msg}", xbmc.LOGINFO)

def debug(msg: str):
    if _enabled():
        xbmc.log(f"[{ADDON_ID}][DEBUG] {msg}", xbmc.LOGINFO)

def warn(msg: str):
    xbmc.log(f"[{ADDON_ID}][WARN] {msg}", xbmc.LOGWARNING)

def error(msg: str):
    xbmc.log(f"[{ADDON_ID}][ERROR] {msg}", xbmc.LOGERROR)
