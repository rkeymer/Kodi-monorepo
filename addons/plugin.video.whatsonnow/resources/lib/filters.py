import re
from . import log

def _split_csv(s: str):
    return [x.strip() for x in (s or '').split(',') if x.strip()]

def build_filter(include_groups='', exclude_groups='', include_keywords='', exclude_keywords='', include_regex='', exclude_regex=''):
    inc_groups=set(x.lower() for x in _split_csv(include_groups))
    exc_groups=set(x.lower() for x in _split_csv(exclude_groups))
    inc_kw=[x.lower() for x in _split_csv(include_keywords)]
    exc_kw=[x.lower() for x in _split_csv(exclude_keywords)]
    inc_re=exc_re=None
    if include_regex:
        try: inc_re=re.compile(include_regex)
        except Exception as e: log.warn(f"Invalid include regex: {include_regex} ({repr(e)})")
    if exclude_regex:
        try: exc_re=re.compile(exclude_regex)
        except Exception as e: log.warn(f"Invalid exclude regex: {exclude_regex} ({repr(e)})")
    def _match(ch: dict) -> bool:
        name=(ch.get('name') or '').strip()
        group=(ch.get('group') or '').strip()
        nl=name.lower(); gl=group.lower()
        if inc_groups and gl not in inc_groups: return False
        if exc_groups and gl in exc_groups: return False
        if inc_kw and not any(k in nl for k in inc_kw): return False
        if exc_kw and any(k in nl for k in exc_kw): return False
        if inc_re and not inc_re.search(name): return False
        if exc_re and exc_re.search(name): return False
        return True
    return _match
