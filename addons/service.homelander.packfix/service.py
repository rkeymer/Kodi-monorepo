# -*- coding: utf-8 -*-
"""
Homelander Pack Fix - self-healing service.

Repairs two third-party add-ons in place so multi-file (season-pack) torrents
resolve to the *requested* episode instead of the largest file:

  1. plugin.video.homelander / resources/lib/modules/sources.py
     Routes magnet sources with a debrid account through Homelander's own
     from_pack machinery (debrid.resolver(..., from_pack='<season>_<episode>'))
     which picks the correct file with matchEpisode(), instead of the blind
     resolveurl.HostedMediaFile(url).resolve() that defaults to the largest file.

  2. script.module.resolveurl / lib/resolveurl/plugins/alldebrid.py
     Prefers the file whose name matches the magnet's dn= parameter before
     falling back to max(sources) (the single-file safety net).

Runs on startup, re-checks periodically, and is idempotent + self-healing:
no-ops when already patched, re-applies if an add-on update reverts the source,
and bails without modifying anything if upstream restructures the anchor lines.
Pure-Python patch helpers (patch_homelander_text / patch_resolveurl_text) take
and return strings and import nothing from Kodi, so they are unit-testable off
a live Kodi install.
"""
import os
import re
import glob

MARK_HL = '_kmr_pack'
MARK_RU = '_kmr_pick'

# Anchors: same patterns used by the standalone PowerShell patchers, so either
# tool can run without fighting the other (both leave an identical marker).
ANCHOR_HL = re.compile(
    r'(?m)^([ \t]*)hmf = resolveurl\.HostedMediaFile\(url\)\r?\n'
    r'[ \t]*if hmf:\r?\n'
    r'[ \t]*u = url = hmf\.resolve\(\)[ \t]*$'
)
ANCHOR_RU = re.compile(r'(?m)^([ \t]*)media_id = max\(sources\)\[1\][ \t]*$')

HL_BLOCK = """# _kmr_pack: multi-file magnet fix - resolve to the requested episode
# instead of letting the debrid resolver default to the largest file.
_kmr_url = None
try:
    if url and 'magnet:' in url.lower() and d:
        _kmr_s = str(getattr(self, 'season', '') or '')
        _kmr_e = str(getattr(self, 'episode', '') or '')
        if not (_kmr_s and _kmr_e):
            try:
                _kmr_meta = json.loads(control.window.getProperty(self.metaProperty))
                _kmr_s = str(_kmr_meta.get('season') or '')
                _kmr_e = str(_kmr_meta.get('episode') or '')
            except:
                pass
        if _kmr_s and _kmr_e:
            _kmr_url = debrid.resolver(url, d, from_pack='%s_%s' % (_kmr_s, _kmr_e))
except:
    _kmr_url = None
if _kmr_url:
    u = url = _kmr_url
else:
    hmf = resolveurl.HostedMediaFile(url)
    if hmf:
        u = url = hmf.resolve()"""

RU_BLOCK = """from urllib.parse import unquote_plus as _kmr_unquote
_kmr_dn = re.search(r'[?&]dn=([^&]+)', media_id, re.I)
_kmr_want = _kmr_unquote(_kmr_dn.group(1)) if _kmr_dn else None
_kmr_pick = None
if _kmr_want:
    for _kmr_link in transfer_info.get('files'):
        for _kmr_e in _kmr_link.get('e') or [_kmr_link]:
            if _kmr_e.get('n') and _kmr_e.get('n').lower() == _kmr_want.lower():
                _kmr_pick = _kmr_e.get('l')
                break
        if _kmr_pick:
            break
media_id = _kmr_pick if _kmr_pick else max(sources)[1]"""


def _reindent(base, block):
    return '\n'.join((base + ln) if ln.strip() else '' for ln in block.split('\n'))


def patch_homelander_text(content):
    """Return patched text, None if already patched, or False if anchor missing."""
    if MARK_HL in content:
        return None
    m = ANCHOR_HL.search(content)
    if not m:
        return False
    repl = _reindent(m.group(1), HL_BLOCK)
    return content[:m.start()] + repl + content[m.end():]


def patch_resolveurl_text(content):
    """Return patched text, None if already patched, or False if anchor missing."""
    if MARK_RU in content:
        return None
    m = ANCHOR_RU.search(content)
    if not m:
        return False
    repl = _reindent(m.group(1), RU_BLOCK)
    return content[:m.start()] + repl + content[m.end():]


def _clear_pyc(target):
    pycache = os.path.join(os.path.dirname(target), '__pycache__')
    stem = os.path.splitext(os.path.basename(target))[0]
    for pyc in glob.glob(os.path.join(pycache, stem + '.cpython-*.pyc')):
        try:
            os.remove(pyc)
        except OSError:
            pass


def apply_patch(path, patch_fn, log):
    """Read, patch, write, and drop stale bytecode. Returns a short status string."""
    if not os.path.exists(path):
        return 'absent'
    try:
        with open(path, 'r', encoding='utf-8', newline='') as fh:
            content = fh.read()
    except Exception as exc:  # noqa: BLE001 - report and move on
        return 'read-error: %s' % exc
    result = patch_fn(content)
    if result is None:
        return 'already-patched'
    if result is False:
        return 'anchor-not-found (needs review)'
    try:
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(result)
    except Exception as exc:  # noqa: BLE001
        return 'write-error: %s' % exc
    _clear_pyc(path)
    log('patched %s' % path)
    return 'patched'


def _targets():
    import xbmcvfs
    home = xbmcvfs.translatePath('special://home/addons/')
    return [
        (
            os.path.join(home, 'plugin.video.homelander', 'resources', 'lib',
                         'modules', 'sources.py'),
            patch_homelander_text,
            'homelander/sources.py',
        ),
        (
            os.path.join(home, 'script.module.resolveurl', 'lib', 'resolveurl',
                         'plugins', 'alldebrid.py'),
            patch_resolveurl_text,
            'resolveurl/alldebrid.py',
        ),
    ]


def sweep():
    import xbmc

    def log(msg):
        xbmc.log('[service.homelander.packfix] %s' % msg, xbmc.LOGINFO)

    summary = []
    for path, fn, label in _targets():
        status = apply_patch(path, fn, log)
        summary.append('%s=%s' % (label, status))
    log('sweep: ' + '  '.join(summary))


def run():
    import xbmc
    monitor = xbmc.Monitor()
    sweep()
    # Re-check hourly so a mid-session Homelander/resolveurl update that reverts
    # the fix is repaired without waiting for the next Kodi restart.
    while not monitor.abortRequested():
        if monitor.waitForAbort(3600):
            break
        sweep()


if __name__ == '__main__':
    run()
