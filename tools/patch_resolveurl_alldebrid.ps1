<#
.SYNOPSIS
Patches script.module.resolveurl's AllDebrid resolver to pick the file whose name
matches the magnet's dn= parameter, instead of blindly picking the largest file
returned for that torrent hash.

.DESCRIPTION
Root cause: resolveurl's alldebrid.py resolves a magnet by uploading it to AllDebrid,
then (when not asked for the full list) picks the single largest video file among
everything AllDebrid returns for that hash:

    media_id = max(sources)[1]

If the underlying torrent/cache entry actually contains more than one episode (season
pack, mislabeled release, etc.), this silently plays the wrong episode - the biggest
file wins regardless of which one was requested. The magnet URI already carries the
correct filename in its dn= parameter; this patch prefers an exact match against that
before falling back to the original max-size behavior.

Idempotent and safe to re-run: no-ops if already patched, and self-heals if a
resolveurl addon update reverts alldebrid.py back to the vulnerable original. If
upstream restructures the code so the anchor line can no longer be found, the script
exits without modifying anything and reports that it needs review.

.PARAMETER KodiAddonsPath
Path to the Kodi addons directory. Defaults to the current user's Kodi addons folder.
#>
param(
    [string]$KodiAddonsPath = (Join-Path $env:APPDATA "Kodi\addons")
)

$ErrorActionPreference = "Stop"

$target = Join-Path $KodiAddonsPath "script.module.resolveurl\lib\resolveurl\plugins\alldebrid.py"

if (-not (Test-Path $target)) {
    Write-Output "NOT FOUND: $target (resolveurl not installed at this path)"
    exit 1
}

$content = Get-Content -Path $target -Raw -Encoding UTF8

if ($content -match '_kmr_pick') {
    Write-Output "ALREADY PATCHED: $target"
    exit 0
}

$anchorPattern = '(?m)^([ \t]*)media_id = max\(sources\)\[1\]\r?$'
$m = [regex]::Match($content, $anchorPattern)
if (-not $m.Success) {
    Write-Output "ANCHOR NOT FOUND: upstream code has changed, patch NOT applied. Needs review: $target"
    exit 2
}

$indent = $m.Groups[1].Value
$replacement = @"
${indent}from urllib.parse import unquote_plus as _kmr_unquote
${indent}_kmr_dn = re.search(r'[?&]dn=([^&]+)', media_id, re.I)
${indent}_kmr_want = _kmr_unquote(_kmr_dn.group(1)) if _kmr_dn else None
${indent}_kmr_pick = None
${indent}if _kmr_want:
${indent}    for _kmr_link in transfer_info.get('files'):
${indent}        for _kmr_e in _kmr_link.get('e') or [_kmr_link]:
${indent}            if _kmr_e.get('n') and _kmr_e.get('n').lower() == _kmr_want.lower():
${indent}                _kmr_pick = _kmr_e.get('l')
${indent}                break
${indent}        if _kmr_pick:
${indent}            break
${indent}media_id = _kmr_pick if _kmr_pick else max(sources)[1]
"@

$patched = $content.Substring(0, $m.Index) + $replacement + $content.Substring($m.Index + $m.Length)

Set-Content -Path $target -Value $patched -Encoding UTF8 -NoNewline

# Clear compiled bytecode so Kodi picks up the patched source on next invocation
$pycacheDir = Join-Path (Split-Path $target -Parent) "__pycache__"
if (Test-Path $pycacheDir) {
    Get-ChildItem $pycacheDir -Filter "alldebrid.cpython-*.pyc" -ErrorAction SilentlyContinue | Remove-Item -Force
}

Write-Output "PATCHED: $target"
