# Keymer Kodi Repository

A monorepo of personal Kodi add-ons, built and published as a self-hosted
Kodi repository on GitHub Pages.

**Repository URL:** https://rkeymer.github.io/Kodi-monorepo/

## Add-ons

| ID | Name | Purpose |
|----|------|---------|
| `plugin.video.whatsonnow` | WhatsOnNow | Live-TV / IPTV browser for Xtream-codes style providers |
| `plugin.video.simkl.watching` | WhatsUpNext | Shows your SIMKL *Watching* list and plays via Homelander / AllDebrid / local files |
| `service.homelander.packfix` | Homelander Pack Fix | Background service, installed as a WhatsUpNext dependency, that self-heals a Homelander/resolveurl season-pack episode-resolution bug |
| `repository.keymer` | Keymer Kodi Repo | The repository add-on that points Kodi at the hosted `addons.xml` |

### WhatsOnNow
Live-TV browser. Point it at an Xtream-codes provider (`base_url` +
`username` / `password`); it fetches the M3U playlist (`get.php`) and XMLTV
EPG (`xmltv.php`). Features: On Now, Coming Up, Favourites, Recently Watched,
Groups, Search, paged All Channels, VOD filtering, local-file caching with
auto-fallback, and a background auto-update service.

### WhatsUpNext
Reads your SIMKL *Watching* list and surfaces New Episodes, Upcoming, and
Movies, with season/episode browsing via TMDB. Selecting an episode hands off
directly to Homelander's stream search; episodes also available in AllDebrid
or a local folder are badged and playable in place.

- **Required:** SIMKL account + Client ID (each user authorises their own);
  [Homelander](https://kodi.tv/) for playback.
- **Optional:** TMDB v3 API key (air dates, posters, search), AllDebrid API
  key, local media folder.

### Homelander Pack Fix
A service (not user-facing) that repairs a bug in the third-party Homelander
add-on: multi-file magnet torrents (season packs) resolve to whatever file the
debrid resolver's `max(sources)` picks — usually the largest file, e.g. always
the pilot — instead of the requested episode. Patches Homelander's resolver to
route through its own (unused) pack-aware `debrid.resolver(..., from_pack=...)`
machinery, and patches resolveurl's AllDebrid plugin to prefer a `dn=`
filename match over largest-file. Runs on startup, checks hourly, no-ops if
Homelander/resolveurl aren't installed, and self-heals if an update to either
add-on reverts the patch. Installed automatically as a WhatsUpNext dependency.

## Building

The build script zips each add-on and generates the repository index
(`addons.xml` + `addons.xml.md5`) into `dist/repo/`:

```sh
python tools/build_repo.py
```

This is the single source of truth — CI runs the same script.

## Publishing

Pushing to `main`/`master` triggers
[`.github/workflows/publish.yml`](.github/workflows/publish.yml), which builds
the repo and deploys `dist/repo/` to the `gh-pages` branch (served at the
repository URL above).

**Workflow:** develop on `dev`, merge to `master` to publish. Bump the
`version` in each add-on's `addon.xml` before merging so Kodi clients see the
update.

## Installing on a Kodi box

1. Download the repository zip (download first, then install — Kodi can't
   browse the GitHub Pages root):
   `https://rkeymer.github.io/Kodi-monorepo/repository.keymer/repository.keymer-1.0.0.zip`
2. Kodi → **Add-ons → Install from zip file** → select the downloaded zip.
3. **Install from repository → Keymer Kodi Repo** → install the add-ons.

**Android TV note:** if Kodi can't see the zip on local/USB storage, grant it
*All files access* in Android app permissions, or copy the zip into Kodi's own
folder (`/storage/emulated/0/Android/data/org.xbmc.kodi/files/`).
