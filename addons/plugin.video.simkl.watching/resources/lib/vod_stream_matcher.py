# -*- coding: utf-8 -*-
"""
VOD Stream Matcher: Loads VOD episodes from WhatsOnNow and finds playable streams
for integration with WhatsUpNext.
"""

import json
import os
import re
import xbmcvfs
import xbmc


def log_debug(msg):
    """Debug logging with WhatsUpNext prefix."""
    xbmc.log(f"[SIMKL Watching] {msg}", xbmc.LOGINFO)


def load_vod_episodes() -> dict:
    """
    Load VOD episodes JSON exported by WhatsOnNow.
    
    Returns:
        dict with items list or empty dict if not found/error
    """
    try:
        addon_data = xbmcvfs.translatePath(
            "special://profile/addon_data/plugin.video.whatsonnow"
        )
        vod_path = os.path.join(addon_data, "cache", "vod_episodes.json")
        
        if not os.path.exists(vod_path):
            log_debug(f"vod_episodes.json not found at {vod_path}")
            return {}
        
        with open(vod_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        items = data.get("items", [])
        generated = data.get("generated_at", 0)
        log_debug(f"Loaded vod_episodes.json: {len(items)} items (generated_at={generated})")
        
        return data
    
    except FileNotFoundError:
        log_debug("vod_episodes.json not found")
        return {}
    except json.JSONDecodeError as e:
        log_debug(f"Failed to parse vod_episodes.json: {repr(e)}")
        return {}
    except Exception as e:
        log_debug(f"Error loading vod_episodes.json: {repr(e)}")
        return {}


def _normalize_series_title(title: str) -> str:
    """
    Normalize series title for better matching.
    Removes: 'The ', years in parentheses, trailing separators.
    """
    if not title:
        return ""
    
    t = title.lower().strip()
    
    # Remove 'the ' prefix
    if t.startswith('the '):
        t = t[4:]
    
    # Remove years in parentheses at the end
    t = re.sub(r'\s*\(\d{4}\)\s*$', '', t)
    
    # Remove other parentheses content at the end
    t = re.sub(r'\s*\([^)]*\)\s*$', '', t)
    
    # Remove trailing separators
    t = re.sub(r'\s*[-|•:]\s*$', '', t)
    
    return t.strip()


def find_stream_for_episode(series_title: str, season: int, episode: int) -> dict:
    """
    Find a playable stream for a given series, season, and episode.
    
    Args:
        series_title: Series name (e.g. "Breaking Bad")
        season: Season number
        episode: Episode number
    
    Returns:
        dict with stream info: {found: bool, name: str, url: str, series: str, season: int, episode: int}
        or empty dict if not found
    """
    vod_data = load_vod_episodes()
    items = vod_data.get("items", [])
    
    if not items:
        log_debug(f"No VOD items to match against for {series_title} S{season:02d}E{episode:02d}")
        return {}
    
    # Normalize series title for matching
    series_norm = _normalize_series_title(series_title)
    
    # Find matching entry
    for item in items:
        item_series_norm = _normalize_series_title(item.get("series") or "")
        item_season = item.get("season")
        item_episode = item.get("episode")
        
        # Match on normalized series name (partial match allowed)
        if series_norm and item_series_norm and series_norm in item_series_norm:
            if item_season == season and item_episode == episode:
                result = {
                    "found": True,
                    "name": item.get("name", ""),
                    "url": item.get("url", ""),
                    "series": item.get("series", ""),
                    "season": season,
                    "episode": episode,
                    "logo": item.get("logo", ""),
                    "group": item.get("group", ""),
                }
                log_debug(f"Stream match found: {result['name']} -> {result['url']}")
                return result
    
    log_debug(f"No stream match found for {series_title} S{season:02d}E{episode:02d}")
    return {}
