# -*- coding: utf-8 -*-
"""
VOD Episode Exporter: Extracts episode/VOD entries from M3U playlists and exports to JSON.
Used by WhatsUpNext integration to find playable streams for next episodes.
"""

import io
import json
import os
import re
import time
import xbmcvfs
import xbmcaddon

from .m3u import parse_extinf, EPISODE_RE, VOD_URL_HINTS
from . import log

ADDON = xbmcaddon.Addon()

# Pattern to match common episode formats: S01E03, S1E3, 1x03, etc.
SEASON_EPISODE_PATTERNS = [
    re.compile(r'S(\d{1,2})\s*E(\d{1,2})', re.IGNORECASE),  # S01E03 or S1E3
    re.compile(r'(\d{1,2})x(\d{1,2})', re.IGNORECASE),      # 1x03
]


def _extract_season_episode(name: str):
    """
    Extract season and episode numbers from a name.
    Returns (season, episode) tuple or None.
    
    Handles formats: S01E03, S1E3, 1x03
    """
    if not name:
        return None
    
    for pattern in SEASON_EPISODE_PATTERNS:
        match = pattern.search(name)
        if match:
            try:
                season = int(match.group(1))
                episode = int(match.group(2))
                return (season, episode)
            except (ValueError, IndexError):
                pass
    
    return None


def _extract_series_title(name: str) -> str:
    """
    Extract series title by taking text before the episode marker.
    
    E.g. "Breaking Bad S01E03" -> "Breaking Bad"
    Also normalizes by removing 'The ', years, etc.
    """
    if not name:
        return ""
    
    # Find the first occurrence of any episode pattern
    for pattern in SEASON_EPISODE_PATTERNS:
        match = pattern.search(name)
        if match:
            # Get text before the match and trim whitespace/separators
            title = name[:match.start()].strip()
            # Remove trailing separators like " - " or " | "
            title = re.sub(r'\s*[-|•]\s*$', '', title)
            
            # Normalize: remove 'the ' prefix, years in parentheses, etc.
            t = title.lower().strip()
            if t.startswith('the '):
                t = t[4:]
            t = re.sub(r'\s*\(\d{4}\)\s*$', '', t)
            t = re.sub(r'\s*\([^)]*\)\s*$', '', t)
            t = re.sub(r'\s*[-|•:]\s*$', '', t)
            return t.strip().title()  # Title case
    
    return name


def _is_vod_entry(url: str, name: str) -> bool:
    """
    Identify if an entry is a VOD/episode entry (not a regular channel).
    Matches the logic from m3u.py _is_vod() function.
    """
    u = (url or "").lower()
    # Check for VOD URL patterns
    if any(h in u for h in VOD_URL_HINTS):
        return True
    # Check for episode pattern in name
    return bool(EPISODE_RE.search(name or ""))


def export_vod_episodes(m3u_bytes: bytes, export_path: str = None) -> dict:
    """
    Parse M3U playlist bytes and export VOD/episode entries to JSON.
    
    Args:
        m3u_bytes: M3U playlist content as bytes
        export_path: Output JSON file path (uses default if None)
    
    Returns:
        dict with export status: {success: bool, count: int, path: str, error: str or None}
    """
    if not m3u_bytes:
        return {"success": False, "count": 0, "error": "Empty playlist data"}
    
    try:
        # Determine export path
        if not export_path:
            addon_data = xbmcvfs.translatePath(
                "special://profile/addon_data/plugin.video.whatsonnow"
            )
            cache_dir = os.path.join(addon_data, "cache")
            export_path = os.path.join(cache_dir, "vod_episodes.json")
        
        # Ensure cache directory exists
        cache_dir = os.path.dirname(export_path)
        if cache_dir and not os.path.exists(cache_dir):
            try:
                os.makedirs(cache_dir, exist_ok=True)
            except Exception as e:
                log.warn(f"Failed to create cache dir {cache_dir}: {repr(e)}")
                return {"success": False, "count": 0, "path": export_path, "error": str(e)}
        
        # Parse M3U
        text = m3u_bytes.decode("utf-8", errors="replace")
        f = io.StringIO(text)
        
        items = []
        pending = None
        
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            
            if line.startswith("#EXTM3U"):
                continue
            
            if line.startswith("#EXTINF"):
                pending = parse_extinf(line)
                continue
            
            # Check for URL line
            if pending and (line.startswith("http://") or line.startswith("https://") or 
                           line.startswith("rtmp://") or line.startswith("rtsp://") or 
                           line.startswith("udp://")):
                url = line
                name = pending.get("name", "")
                
                # Identify VOD entries
                if _is_vod_entry(url, name):
                    se = _extract_season_episode(name)
                    if se:  # Only export entries with extractable season/episode
                        season, episode = se
                        series = _extract_series_title(name)
                        
                        item = {
                            "series": series,
                            "season": season,
                            "episode": episode,
                            "name": name,
                            "url": url,
                            "logo": pending.get("logo", ""),
                            "group": pending.get("group", ""),
                        }
                        items.append(item)
                
                pending = None
                continue
            
            if pending and not line.startswith("#"):
                pending = None
        
        # Write JSON
        output = {
            "generated_at": int(time.time()),
            "items": items,
        }
        
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        
        log.info(f"Exported {len(items)} VOD episodes to {export_path}")
        return {"success": True, "count": len(items), "path": export_path}
    
    except Exception as e:
        log.warn(f"VOD export failed: {repr(e)}")
        return {"success": False, "count": 0, "error": str(e)}
