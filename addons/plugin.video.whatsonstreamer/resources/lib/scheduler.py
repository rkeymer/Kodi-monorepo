import os
import json
import time
import uuid

import xbmcvfs

ADDON_DATA_DIR = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.whatsonstreamer')
SCHEDULE_PATH = os.path.join(ADDON_DATA_DIR, 'scheduled_events.json')


def _ensure_dir():
    if not os.path.exists(ADDON_DATA_DIR):
        os.makedirs(ADDON_DATA_DIR, exist_ok=True)


def load_scheduled() -> list:
    _ensure_dir()
    if not os.path.exists(SCHEDULE_PATH):
        return []
    try:
        with open(SCHEDULE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f) or []
    except Exception:
        return []


def save_scheduled(items: list):
    _ensure_dir()
    with open(SCHEDULE_PATH, 'w', encoding='utf-8') as f:
        json.dump(items or [], f, ensure_ascii=False)


def add_scheduled(channel_url: str, channel_name: str, channel_logo: str, title: str, start: int, stop: int) -> dict:
    items = load_scheduled()
    for it in items:
        if (it.get('channel_url') or '') == (channel_url or '') and int(it.get('start', 0)) == int(start):
            return it

    entry = {
        'id': uuid.uuid4().hex[:12],
        'channel_url': channel_url or '',
        'channel_name': channel_name or 'Channel',
        'channel_logo': channel_logo or '',
        'title': title or '(untitled)',
        'start': int(start),
        'stop': int(stop),
        'notified': False,
        'created': int(time.time()),
    }
    items.append(entry)
    save_scheduled(items)
    return entry


def remove_scheduled(event_id: str):
    items = [it for it in load_scheduled() if it.get('id') != event_id]
    save_scheduled(items)


def due_events(now: int, grace_seconds: int = 600) -> list:
    due = []
    for it in load_scheduled():
        if it.get('notified'):
            continue
        start = int(it.get('start', 0))
        if start <= now < start + grace_seconds:
            due.append(it)
    return due


def mark_notified(event_id: str):
    items = load_scheduled()
    for it in items:
        if it.get('id') == event_id:
            it['notified'] = True
    save_scheduled(items)


def prune_expired(now: int, past_seconds: int = 3600) -> list:
    items = load_scheduled()
    kept = [it for it in items if int(it.get('stop', 0)) >= (now - past_seconds)]
    if len(kept) != len(items):
        save_scheduled(kept)
    return kept
