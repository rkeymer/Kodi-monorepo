import sys
import urllib.parse
import xbmcgui
import xbmcplugin

HANDLE = int(sys.argv[1])


def get_params():
    """
    sys.argv[2] contains the querystring portion passed by Kodi, e.g.
    '?action=new'
    """
    if len(sys.argv) < 3 or not sys.argv[2]:
        return {}
    return dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?')))


def build_url(**kwargs):
    """
    Builds a plugin URL like:
    plugin://plugin.video.whatsonstreamer/?action=new
    """
    return sys.argv[0] + "?" + urllib.parse.urlencode(kwargs)


def add_folder(label, action, icon=None):
    li = xbmcgui.ListItem(label=label)
    if icon:
        li.setArt({"icon": icon, "thumb": icon})
    url = build_url(action=action)
    xbmcplugin.addDirectoryItem(
        handle=HANDLE,
        url=url,
        listitem=li,
        isFolder=True
    )


def add_item(label, url="", info=None, art=None, is_folder=False, context_menu=None, label2="", cast=None, is_playable=False, replace_context=False):
    li = xbmcgui.ListItem(label=label, label2=label2)
    if is_playable:
        li.setProperty("IsPlayable", "true")
    if info:
        li.setInfo("video", info)
    if art:
        li.setArt(art)
    if cast:
        try:
            li.setCast(cast)
        except Exception:
            pass
    if context_menu:
        li.addContextMenuItems(context_menu, replaceItems=replace_context)
    xbmcplugin.addDirectoryItem(
        handle=HANDLE,
        url=url,
        listitem=li,
        isFolder=is_folder
    )


def end_dir():
    xbmcplugin.endOfDirectory(HANDLE)
