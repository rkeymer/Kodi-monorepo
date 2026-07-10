import time

import xbmc

from resources.lib.cache import DiskCache

_cache_recs = DiskCache("whatsupnext_recs", ttl=30 * 86400)
_KEY = "current"

MAX_ITEMS = 20
_MAX_BECAUSE = 3

ENGLISH_LANGUAGE = "en"
_CANDIDATE_POOL_MULTIPLIER = 3  # wider pre-filter pool so English-only filtering still fills MAX_ITEMS


def save(data: dict):
    _cache_recs.set(_KEY, data)


def load():
    return _cache_recs.get(_KEY)


def remove_item(kind: str, simkl_id: int) -> bool:
    """Drops one item from the saved Recommended list (e.g. after the user marks it
    'dropped' in SIMKL) so it disappears immediately instead of waiting for the next
    background warm cycle. Returns True if something was actually removed."""
    data = load()
    if not data:
        return False
    list_key = "shows" if kind == "show" else "movies"
    items = data.get(list_key) or []
    filtered = [i for i in items if i.get("simkl_id") != simkl_id]
    if len(filtered) == len(items):
        return False
    data[list_key] = filtered
    save(data)
    return True


def _safe(fn, label):
    try:
        return fn()
    except Exception as e:
        xbmc.log(f"[WhatsOnStreamer][Recommendations] {label} failed: {e}", xbmc.LOGWARNING)
        return None


def _extract_show_entries(data):
    """Yields (simkl_id, title, year, poster) from a shows/watching or shows/completed response."""
    items = data.get("shows", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    for it in items or []:
        show = (it or {}).get("show") or {}
        ids = show.get("ids") or {}
        sid = ids.get("simkl")
        if not sid:
            continue
        yield int(sid), show.get("title") or "", show.get("year"), show.get("poster")


def _extract_movie_entries(data):
    """Yields (simkl_id, title, year, poster) from a movies/plan or movies/completed response.
    Response shape isn't guaranteed consistent (nested under 'movies'/'items'/'data', or a bare
    list) - mirrors main.py's show_movies() defensive parsing of the same family of endpoints."""
    movies = []
    if isinstance(data, dict):
        for key in ("movies", "items", "data"):
            if isinstance(data.get(key), list):
                movies = data[key]
                break
    elif isinstance(data, list):
        movies = data

    for it in movies:
        if not isinstance(it, dict):
            continue
        movie = it.get("movie") or it.get("film") or it
        if not isinstance(movie, dict):
            continue
        ids = movie.get("ids") or {}
        sid = ids.get("simkl")
        if not sid:
            continue
        yield int(sid), movie.get("title") or movie.get("name") or "", movie.get("year"), (
            movie.get("poster") or movie.get("poster_path")
        )


def _original_language(tmdb, kind: str, tmdb_id):
    """TMDB's original_language (ISO 639-1) for a tmdb_id, or None if it can't be
    determined (missing id or the lookup failed). Both tv_details()/movie_details()
    are disk-cached, so this costs nothing once warm."""
    if not tmdb_id:
        return None
    try:
        tmdb_id_int = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    if kind == "show":
        details = _safe(lambda: tmdb.tv_details(tmdb_id_int), f"TMDB tv_details {tmdb_id_int}")
    else:
        details = _safe(lambda: tmdb.movie_details(tmdb_id_int), f"TMDB movie_details {tmdb_id_int}")
    return (details or {}).get("original_language")


def _finalize(simkl, tmdb, scores: dict, kind: str):
    """Ranks a {simkl_id: {count, because, title, year, poster}} bucket and backfills
    tmdb/imdb ids + overview + rating via one more (cached) details fetch per candidate
    - needed so the result can link into the existing show/movie detail screens like
    every other WhatsUpNext entry does.

    English-only filter: TMDB's original_language must be 'en'. This also rules out
    dubbed foreign-language content - SIMKL/TMDB catalog a dub under the same
    (foreign-original) entry rather than as a separate English title, so excluding
    non-English originals excludes their dubs too. Considers a wider candidate pool
    than MAX_ITEMS so filtering still leaves close to a full list. If TMDB isn't
    configured at all the filter is skipped entirely (an always-empty list would be
    worse than an unfiltered one); otherwise a candidate whose language can't be
    resolved is excluded, erring toward the "ensure English" guarantee.
    """
    filter_active = tmdb is not None and tmdb.is_configured()
    pool_size = MAX_ITEMS * _CANDIDATE_POOL_MULTIPLIER if filter_active else MAX_ITEMS

    ranked = sorted(
        scores.items(),
        key=lambda kv: (-kv[1]["count"], (kv[1]["title"] or "").lower()),
    )[:pool_size]

    out = []
    for rid, entry in ranked:
        if len(out) >= MAX_ITEMS:
            break

        if kind == "show":
            detail = _safe(lambda: simkl.get_show_details_full(rid), f"backfill show {rid}") or {}
        else:
            detail = _safe(lambda: simkl.get_movie_details_full(rid), f"backfill movie {rid}") or {}

        ids = detail.get("ids") or {}
        tmdb_id = ids.get("tmdb", "")

        if filter_active and _original_language(tmdb, kind, tmdb_id) != ENGLISH_LANGUAGE:
            continue

        simkl_rating = (detail.get("ratings") or {}).get("simkl") or {}

        out.append({
            "simkl_id": rid,
            "title": detail.get("title") or entry["title"],
            "year": detail.get("year") or entry["year"] or "",
            "poster": detail.get("poster") or entry["poster"],
            "imdb_id": ids.get("imdb", ""),
            "tmdb_id": tmdb_id,
            "overview": detail.get("overview") or "",
            "rating": simkl_rating.get("rating") or 0.0,
            "votes": simkl_rating.get("votes") or 0,
            "count": entry["count"],
            "because": entry["because"],
        })
    return out


def build(simkl, tmdb=None) -> dict:
    """Builds the "Recommended" lists from the account's SIMKL watch history.

    SIMKL has no personalized "for you" feed - instead every show/movie details
    response carries a `users_recommendations` array ("people who watched this also
    watched"). This sweeps completed+watching shows and completed movies as seeds,
    tallies each seed's recommendations by the recommendation's own type (a movie can
    recommend a show, e.g. Serenity -> Firefly), excludes anything already watched or
    planned, and keeps the top MAX_ITEMS per bucket with a "because you watched" trail.

    `tmdb` (a TmdbApi instance) is optional but should be passed so _finalize() can
    apply the English-only filter - see _finalize()'s docstring.
    """
    completed_shows_data = _safe(simkl.get_completed_shows, "completed shows fetch") or {}
    watching_shows_data = _safe(simkl.get_watching_shows, "watching shows fetch") or {}
    dropped_shows_data = _safe(simkl.get_dropped_shows, "dropped shows fetch") or {}
    completed_movies_data = _safe(simkl.get_completed_movies, "completed movies fetch") or {}
    plan_movies_data = _safe(simkl.get_plan_movies, "plan movies fetch") or {}
    dropped_movies_data = _safe(simkl.get_dropped_movies, "dropped movies fetch") or {}

    completed_shows = list(_extract_show_entries(completed_shows_data))
    watching_shows = list(_extract_show_entries(watching_shows_data))
    dropped_shows = list(_extract_show_entries(dropped_shows_data))
    completed_movies = list(_extract_movie_entries(completed_movies_data))
    plan_movies = list(_extract_movie_entries(plan_movies_data))
    dropped_movies = list(_extract_movie_entries(dropped_movies_data))

    # Dropped items are excluded but never used as seeds - a show/movie the user
    # explicitly rejected shouldn't also drive further recommendations.
    known_show_ids = (
        {sid for sid, *_ in completed_shows} | {sid for sid, *_ in watching_shows} | {sid for sid, *_ in dropped_shows}
    )
    known_movie_ids = (
        {sid for sid, *_ in completed_movies} | {sid for sid, *_ in plan_movies} | {sid for sid, *_ in dropped_movies}
    )

    seeds = (
        [("show", sid, title) for sid, title, *_ in completed_shows]
        + [("show", sid, title) for sid, title, *_ in watching_shows]
        + [("movie", sid, title) for sid, title, *_ in completed_movies]
    )

    show_scores, movie_scores = {}, {}
    seen_seeds = set()

    for kind, sid, seed_title in seeds:
        if (kind, sid) in seen_seeds:
            continue
        seen_seeds.add((kind, sid))

        if kind == "show":
            detail = _safe(lambda: simkl.get_show_details_full(sid), f"seed show details {sid}")
        else:
            detail = _safe(lambda: simkl.get_movie_details_full(sid), f"seed movie details {sid}")
        if not detail:
            continue

        for rec in detail.get("users_recommendations") or []:
            rid = (rec.get("ids") or {}).get("simkl")
            if not rid:
                continue
            rid = int(rid)
            rtype = rec.get("type")

            if rtype in ("tv", "anime"):
                if rid in known_show_ids:
                    continue
                bucket = show_scores
            elif rtype == "movie":
                if rid in known_movie_ids:
                    continue
                bucket = movie_scores
            else:
                continue

            entry = bucket.get(rid)
            if entry is None:
                entry = {
                    "count": 0,
                    "because": [],
                    "title": rec.get("title") or "",
                    "year": rec.get("year"),
                    "poster": rec.get("poster"),
                }
                bucket[rid] = entry
            entry["count"] += 1
            if seed_title and seed_title not in entry["because"] and len(entry["because"]) < _MAX_BECAUSE:
                entry["because"].append(seed_title)

    shows_out = _finalize(simkl, tmdb, show_scores, kind="show")
    movies_out = _finalize(simkl, tmdb, movie_scores, kind="movie")

    xbmc.log(
        f"[WhatsOnStreamer][Recommendations] built {len(shows_out)} shows / {len(movies_out)} movies "
        f"from {len(seen_seeds)} seeds",
        xbmc.LOGINFO,
    )

    return {"shows": shows_out, "movies": movies_out, "generated_at": int(time.time())}
