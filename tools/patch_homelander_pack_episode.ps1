<#
.SYNOPSIS
Patches plugin.video.homelander's sources.py so multi-file torrent magnets resolve
to the requested episode instead of whatever file the debrid resolver picks by default.

.DESCRIPTION
Root cause: Homelander's sourcesResolve() has its pack-aware resolution block commented
out, so every source - including magnets whose torrents contain a whole season - is
resolved via a bare:

    hmf = resolveurl.HostedMediaFile(url)
    if hmf:
        u = url = hmf.resolve()

resolveurl's debrid magnet resolution picks the single largest video file in the
torrent (AllDebrid: media_id = max(sources)[1]). In a season pack the largest file is
often not the requested episode (Silicon Valley S01: the pilot is the longest, so
every episode request plays S01E01). Homelander already ships working pack machinery -
debrid.resolver(url, d, from_pack='<season>_<episode>') picks the correct file with
matchEpisode() and unlocks that file's link directly - but nothing calls it.

This patch re-routes magnet sources with a debrid account through that machinery when
the requested season/episode is known (from self.season/self.episode, falling back to
the meta window property for the addItem/playItem path). If no file in the torrent
matches the episode, or anything errors, it falls back to the original blind resolve,
so behavior degrades to exactly what happens today.

Idempotent and safe to re-run: no-ops if already patched, and self-heals if a
Homelander addon update reverts sources.py. If upstream restructures the code so the
anchor lines can no longer be found, the script exits without modifying anything and
reports that it needs review.

.PARAMETER KodiAddonsPath
Path to the Kodi addons directory. Defaults to the current user's Kodi addons folder.
#>
param(
    [string]$KodiAddonsPath = (Join-Path $env:APPDATA "Kodi\addons")
)

$ErrorActionPreference = "Stop"

$target = Join-Path $KodiAddonsPath "plugin.video.homelander\resources\lib\modules\sources.py"

if (-not (Test-Path $target)) {
    Write-Output "NOT FOUND: $target (plugin.video.homelander not installed at this path)"
    exit 1
}

$content = Get-Content -Path $target -Raw -Encoding UTF8

if ($content -match '_kmr_pack') {
    Write-Output "ALREADY PATCHED: $target"
    exit 0
}

$anchorPattern = '(?m)^([ \t]*)hmf = resolveurl\.HostedMediaFile\(url\)\r?\n[ \t]*if hmf:\r?\n[ \t]*u = url = hmf\.resolve\(\)\r?$'
$m = [regex]::Match($content, $anchorPattern)
if (-not $m.Success) {
    Write-Output "ANCHOR NOT FOUND: upstream code has changed, patch NOT applied. Needs review: $target"
    exit 2
}

$i = $m.Groups[1].Value
$replacement = @"
${i}# _kmr_pack: multi-file magnet fix - resolve to the requested episode
${i}# instead of letting the debrid resolver default to the largest file.
${i}_kmr_url = None
${i}try:
${i}    if url and 'magnet:' in url.lower() and d:
${i}        _kmr_s = str(getattr(self, 'season', '') or '')
${i}        _kmr_e = str(getattr(self, 'episode', '') or '')
${i}        if not (_kmr_s and _kmr_e):
${i}            try:
${i}                _kmr_meta = json.loads(control.window.getProperty(self.metaProperty))
${i}                _kmr_s = str(_kmr_meta.get('season') or '')
${i}                _kmr_e = str(_kmr_meta.get('episode') or '')
${i}            except:
${i}                pass
${i}        if _kmr_s and _kmr_e:
${i}            _kmr_url = debrid.resolver(url, d, from_pack='%s_%s' % (_kmr_s, _kmr_e))
${i}except:
${i}    _kmr_url = None
${i}if _kmr_url:
${i}    u = url = _kmr_url
${i}else:
${i}    hmf = resolveurl.HostedMediaFile(url)
${i}    if hmf:
${i}        u = url = hmf.resolve()
"@

$patched = $content.Substring(0, $m.Index) + $replacement + $content.Substring($m.Index + $m.Length)

Set-Content -Path $target -Value $patched -Encoding UTF8 -NoNewline

# Clear compiled bytecode so Kodi picks up the patched source on next invocation
$pycacheDir = Join-Path (Split-Path $target -Parent) "__pycache__"
if (Test-Path $pycacheDir) {
    Get-ChildItem $pycacheDir -Filter "sources.cpython-*.pyc" -ErrorAction SilentlyContinue | Remove-Item -Force
}

Write-Output "PATCHED: $target"
