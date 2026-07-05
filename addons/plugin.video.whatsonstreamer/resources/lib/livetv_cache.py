import os, json, time
import xbmcvfs

def _real(p: str) -> str:
    return xbmcvfs.translatePath(p) if p else ''

def ensure_dir(p: str):
    rp=_real(p)
    if rp and not os.path.exists(rp):
        os.makedirs(rp, exist_ok=True)

def is_fresh(p: str, ttl_seconds: int) -> bool:
    rp=_real(p)
    if not rp or not os.path.exists(rp):
        return False
    try:
        return (time.time()-os.path.getmtime(rp)) <= max(0,int(ttl_seconds))
    except Exception:
        return False

def load_json(p: str):
    rp=_real(p)
    with open(rp,'r',encoding='utf-8') as f:
        return json.load(f)

def save_json(p: str, data):
    rp=_real(p)
    parent=os.path.dirname(rp)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    with open(rp,'w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False)
