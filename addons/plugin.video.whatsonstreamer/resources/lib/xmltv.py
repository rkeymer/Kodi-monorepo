import calendar
import re
import time
import xml.etree.ElementTree as ET
import xbmcvfs

# Sports EPG titles frequently abbreviate teams to 3-letter codes ("RSA v SCO",
# "NZL v ITA") instead of full names. Grouped so any member of a group is treated
# as equivalent to every other member for fixture search - see _fixture_matches().
_TEAM_GROUPS = [
    {"south africa", "rsa", "springboks", "boks"},
    {"scotland", "sco"},
    {"england", "eng"},
    {"ireland", "ire"},
    {"wales", "wal"},
    {"france", "fra"},
    {"new zealand", "nzl", "all blacks"},
    {"argentina", "arg"},
    {"australia", "aus", "wallabies"},
    {"italy", "ita"},
    {"japan", "jpn"},
    {"fiji", "fij"},
    {"georgia", "geo"},
    {"samoa", "sam"},
    {"tonga", "ton"},
    {"canada", "can"},
    {"uruguay", "uru"},
    {"spain", "esp"},
    {"portugal", "por"},
    {"romania", "rou"},
    {"usa", "united states"},
    {"chile", "chi"},
    {"namibia", "nam"},
    {"germany", "ger"},
    {"netherlands", "ned"},
]
_TEAM_ALIASES = {}
for _group in _TEAM_GROUPS:
    for _name in _group:
        _TEAM_ALIASES[_name] = _group

_VS_SPLIT_RE = re.compile(r'\s+(?:vs\.?|versus|v)\s+', re.IGNORECASE)


def _split_fixture(text: str):
    """Splits 'South Africa vs Scotland' / 'RSA v SCO' into (left, right) lowercase
    parts, or None if `text` doesn't look like a two-team fixture."""
    parts = _VS_SPLIT_RE.split(text.strip(), maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None
    return parts[0].strip().lower(), parts[1].strip().lower()


def _side_matches(query_side: str, title_side: str) -> bool:
    if query_side in title_side or title_side in query_side:
        return True
    for alias in _TEAM_ALIASES.get(query_side, ()):
        if alias in title_side or title_side in alias:
            return True
    return False


def _fixture_matches(query: str, title_lower: str) -> bool:
    """True if `query` ('south africa vs scotland') matches `title_lower`
    ('rsa v sco') as a two-team fixture, regardless of team-code abbreviation or
    which side either team is listed on. Only engages when the query itself is
    also a two-team pattern, so plain single-word/free-text searches are
    untouched by this."""
    q_pair = _split_fixture(query)
    if not q_pair:
        return False
    t_pair = _split_fixture(title_lower)
    if not t_pair:
        return False
    q1, q2 = q_pair
    t1, t2 = t_pair
    return (
        (_side_matches(q1, t1) and _side_matches(q2, t2))
        or (_side_matches(q1, t2) and _side_matches(q2, t1))
    )


def _parse_xmltv_time(s: str) -> int:
    if not s:
        return 0
    s = s.strip()
    tz = None
    if ' ' in s:
        base, tz = s.split(' ', 1)
        tz = tz.strip()
    else:
        base = s
    try:
        year=int(base[0:4]); mon=int(base[4:6]); day=int(base[6:8]); hour=int(base[8:10]); minute=int(base[10:12]); sec=int(base[12:14]) if len(base)>=14 else 0
    except Exception:
        return 0
    tup=(year,mon,day,hour,minute,sec,0,0,0)
    # calendar.timegm is the correct UTC-tuple-to-epoch inverse of time.gmtime.
    # time.timegm does NOT exist in the standard library (this used to fall back
    # to time.mktime(tup), which interprets the tuple as LOCAL time instead of
    # UTC - silently shifting every parsed EPG time by the device's local UTC
    # offset, in every screen that reads it: On Now, Coming Up, EPG search,
    # scheduled channel switching).
    epoch = calendar.timegm(tup)
    if tz and tz != 'Z':
        try:
            sign = 1 if tz[0] == '+' else -1
            hh=int(tz[1:3]); mm=int(tz[3:5])
            epoch -= sign*(hh*3600+mm*60)
        except Exception:
            pass
    return int(epoch)

def extract_now_next_from_file(xml_path: str, wanted_channel_ids: set, now_epoch: int = None) -> dict:
    if now_epoch is None:
        now_epoch=int(time.time())
    out={cid:{'now':None,'next':None} for cid in wanted_channel_ids}
    done=set()
    real_path=xbmcvfs.translatePath(xml_path)
    with open(real_path,'rb') as f:
        for _, elem in ET.iterparse(f, events=('end',)):
            if elem.tag!='programme':
                continue
            cid=elem.attrib.get('channel')
            if not cid or cid not in out or cid in done:
                elem.clear(); continue
            start=_parse_xmltv_time(elem.attrib.get('start',''))
            stop=_parse_xmltv_time(elem.attrib.get('stop',''))
            if stop<=now_epoch:
                elem.clear(); continue
            slot=out[cid]
            if start<=now_epoch<stop:
                title_el=elem.find('title')
                title=(title_el.text or '').strip() if title_el is not None and title_el.text else ''
                slot['now']={'title':title,'start':start,'stop':stop}
            elif start>now_epoch:
                nextp=slot['next']
                if nextp is None or start<nextp['start']:
                    title_el=elem.find('title')
                    title=(title_el.text or '').strip() if title_el is not None and title_el.text else ''
                    slot['next']={'title':title,'start':start,'stop':stop}
            if slot['now'] is not None and slot['next'] is not None:
                done.add(cid)
            elem.clear()
            if len(done)==len(out):
                break
    return out

def extract_schedule_from_file(xml_path: str, wanted_channel_ids: set, start_epoch: int, end_epoch: int, max_per_channel: int = 50) -> dict:
    out = {cid: [] for cid in wanted_channel_ids}
    counts = {cid: 0 for cid in wanted_channel_ids}
    real_path = xbmcvfs.translatePath(xml_path)
    with open(real_path, 'rb') as f:
        for _, elem in ET.iterparse(f, events=('end',)):
            if elem.tag != 'programme':
                continue
            cid = elem.attrib.get('channel')
            if not cid or cid not in out:
                elem.clear();
                continue
            if counts[cid] >= max_per_channel:
                elem.clear();
                continue
            start = _parse_xmltv_time(elem.attrib.get('start', ''))
            stop = _parse_xmltv_time(elem.attrib.get('stop', ''))
            if start < end_epoch and stop > start_epoch:
                title_el = elem.find('title')
                title = (title_el.text or '').strip() if title_el is not None and title_el.text else ''
                out[cid].append({'title': title, 'start': start, 'stop': stop})
                counts[cid] += 1
            elem.clear()
    for cid in out:
        out[cid].sort(key=lambda p: p.get('start', 0))
    return out

def search_programmes_from_file(xml_path: str, query: str, start_epoch: int, end_epoch: int, max_results: int = 200) -> list:
    """Scan every channel's schedule (not just a wanted subset) for a title match
    within the time window. Used by EPG search - unlike extract_schedule_from_file,
    the channel set isn't known up front since we're looking for whichever channel(s)
    happen to be airing a matching programme."""
    ql = (query or '').strip().lower()
    if not ql:
        return []
    out = []
    real_path = xbmcvfs.translatePath(xml_path)
    with open(real_path, 'rb') as f:
        for _, elem in ET.iterparse(f, events=('end',)):
            if elem.tag != 'programme':
                continue
            if len(out) >= max_results:
                elem.clear();
                continue
            cid = elem.attrib.get('channel')
            if not cid:
                elem.clear(); continue
            start = _parse_xmltv_time(elem.attrib.get('start', ''))
            stop = _parse_xmltv_time(elem.attrib.get('stop', ''))
            if not (start < end_epoch and stop > start_epoch):
                elem.clear(); continue
            title_el = elem.find('title')
            title = (title_el.text or '').strip() if title_el is not None and title_el.text else ''
            title_lower = title.lower()
            if ql in title_lower or _fixture_matches(ql, title_lower):
                out.append({'channel': cid, 'title': title, 'start': start, 'stop': stop})
            elem.clear()
    out.sort(key=lambda p: p.get('start', 0))
    return out
