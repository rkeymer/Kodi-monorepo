param(
  [string]$RepoRoot = "C:\dev\Kodi-monorepo",
  [string]$OutDir   = "dist"
)

$ErrorActionPreference = "Stop"

function Get-AddonVersion([string]$addonXmlPath) {
  if (!(Test-Path $addonXmlPath)) { throw "Missing addon.xml: $addonXmlPath" }
  [xml]$xml = Get-Content $addonXmlPath -Raw
  return $xml.addon.version
}

function New-AddonZip([string]$addonFolderPath, [string]$outZipPath) {
  if (!(Test-Path $addonFolderPath)) { throw "Missing addon folder: $addonFolderPath" }

  $addonName = Split-Path $addonFolderPath -Leaf

  # stage into temp so zip root is the addon folder name
  $tempRoot  = Join-Path ([System.IO.Path]::GetTempPath()) ("kodi_zip_" + [guid]::NewGuid().ToString("N"))
  $tempAddon = Join-Path $tempRoot $addonName
  New-Item -ItemType Directory -Path $tempAddon -Force | Out-Null

  # copy files cleanly
  robocopy $addonFolderPath $tempAddon /MIR `
    /XD ".git" ".vscode" "__pycache__" ".pytest_cache" "dist" "tools" `
    /XF "*.pyc" "*.pyo" "*.zip" `
    /NFL /NDL /NJH /NJS | Out-Null

  if (Test-Path $outZipPath) { Remove-Item $outZipPath -Force }

  # IMPORTANT: zip must include the top-level addon folder
  Compress-Archive -Path (Join-Path $tempRoot $addonName) -DestinationPath $outZipPath -Force

  Remove-Item $tempRoot -Recurse -Force
}

$repoRootPath = (Resolve-Path $RepoRoot).Path
$outPath      = Join-Path $repoRootPath $OutDir
New-Item -ItemType Directory -Path $outPath -Force | Out-Null

$addonsPath = Join-Path $repoRootPath "addons"

$addonIds = @(
  "plugin.video.whatsonnow",
  "plugin.video.simkl.watching"
)

$built = @()

foreach ($id in $addonIds) {
  $addonFolder = Join-Path $addonsPath $id
  $ver = Get-AddonVersion (Join-Path $addonFolder "addon.xml")

  $zipName = "$id-$ver.zip"
  $zipPath = Join-Path $outPath $zipName

  Write-Host "Building $id v$ver -> $zipName"
  New-AddonZip -addonFolderPath $addonFolder -outZipPath $zipPath

  $built += $zipPath
}

Write-Host ""
Write-Host "Build complete:"
$built | ForEach-Object { Write-Host " - $_" }