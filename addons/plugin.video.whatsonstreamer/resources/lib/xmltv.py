import time
import xml.etree.ElementTree as ET
import xbmcvfs

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
    timegm = getattr(time, 'timegm', None)
    epoch = timegm(tup) if timegm else int(time.mktime(tup))
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
