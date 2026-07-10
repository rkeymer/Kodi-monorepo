# -*- coding: utf-8 -*-
"""Finds alternate channels to offer when a selected channel fails its
pre-flight check (see livetv.py's _resolve_channel_playback).

Three kinds of alternate, in priority order:
  1. Backup feeds - explicitly tagged by the provider as a backup for the
     same channel (confirmed via the live playlist: the real-world
     convention is a bracketed "[BK]" / "[BK1]" suffix, e.g.
     "UK| TNT SPORT 1 HD [BK]" - not free-text "bck"/"backup").
  2. EPG-title matches - a different channel currently airing the exact
     same programme title.
  3. Other same-name matches - fuzzy-normalized name equality, catching
     cross-provider duplicates (e.g. "CNN International" from a second
     provider group) that aren't tagged as a backup.
"""
import re

_NOISE_TOKENS = {'hd', 'fhd', 'uhd', '4k', 'sd'}

_BK_TAG_RE = re.compile(r'\[\s*bk\d*\s*\]', re.IGNORECASE)
_PREFIX_RE = re.compile(r'^[a-z0-9]{2,6}\s*[|:]\s*', re.IGNORECASE)
_BRACKET_RE = re.compile(r'[\(\[\{][^\)\]\}]*[\)\]\}]')
_NONALNUM_RE = re.compile(r'[^a-z0-9 ]+')
_WS_RE = re.compile(r'\s+')

MAX_CANDIDATES = 12


def is_backup_tagged(name: str) -> bool:
    return bool(_BK_TAG_RE.search(name or ''))


def normalize_channel_name(name: str) -> str:
    n = (name or '').lower()
    n = _PREFIX_RE.sub('', n)
    n = _BRACKET_RE.sub(' ', n)
    n = _NONALNUM_RE.sub(' ', n)
    tokens = [t for t in n.split() if t not in _NOISE_TOKENS]
    # Joined with no separator (not a single space) - real playlists spell the
    # same brand inconsistently, e.g. "DSTV| SUPER SPORT RUGBY HD" vs
    # "ZA| SUPERSPORT RUGBY FHD", or "BEIN SPORTS" vs "BEIN-SPORTS". Digits
    # stay as their own token either way, so "Sky Sports 1" still won't merge
    # with "Sky Sports 2".
    return ''.join(tokens)


def find_failover_candidates(channels: list, epg_map: dict, failed_url: str, failed_name: str,
                              failed_tvg_id: str = '', max_results: int = MAX_CANDIDATES) -> list:
    seen_urls = {failed_url or ''}
    backups, epg_hits, name_hits = [], [], []

    target_norm = normalize_channel_name(failed_name)

    if target_norm:
        for i, ch in enumerate(channels):
            u = ch.get('url') or ''
            if not u or u in seen_urls:
                continue
            raw_name = ch.get('name') or ''
            if is_backup_tagged(raw_name) and normalize_channel_name(raw_name) == target_norm:
                backups.append({'index': i, 'channel': ch, 'reason': 'backup', 'match_title': None})
                seen_urls.add(u)

    now_title = ''
    if failed_tvg_id:
        slot = (epg_map or {}).get(failed_tvg_id)
        if slot:
            now = slot.get('now')
            if now:
                now_title = (now.get('title') or '').strip()

    if now_title:
        nt_l = now_title.lower()
        for i, ch in enumerate(channels):
            u = ch.get('url') or ''
            if not u or u in seen_urls:
                continue
            cid = (ch.get('tvg_id') or '').strip()
            if not cid or cid == failed_tvg_id:
                continue
            slot = (epg_map or {}).get(cid)
            if not slot:
                continue
            now = slot.get('now')
            if now and (now.get('title') or '').strip().lower() == nt_l:
                epg_hits.append({'index': i, 'channel': ch, 'reason': 'epg', 'match_title': now.get('title')})
                seen_urls.add(u)

    if target_norm:
        for i, ch in enumerate(channels):
            u = ch.get('url') or ''
            if not u or u in seen_urls:
                continue
            if normalize_channel_name(ch.get('name') or '') == target_norm:
                name_hits.append({'index': i, 'channel': ch, 'reason': 'name', 'match_title': None})
                seen_urls.add(u)

    return (backups + epg_hits + name_hits)[:max_results]
