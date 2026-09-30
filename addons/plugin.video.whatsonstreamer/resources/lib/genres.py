# TMDB's own genre taxonomies. Movie and TV genres are different id spaces
# (and different label sets - e.g. TV has no standalone "Thriller" genre,
# movies have no "Reality"), so each kind gets its own list rather than one
# shared table. Animation happens to share id 16 across both.

ANIMATION_GENRE_ID = 16

MOVIE_GENRES = [
    ("Action", 28),
    ("Adventure", 12),
    ("Animation", 16),
    ("Comedy", 35),
    ("Crime", 80),
    ("Documentary", 99),
    ("Drama", 18),
    ("Family", 10751),
    ("Fantasy", 14),
    ("History", 36),
    ("Horror", 27),
    ("Music", 10402),
    ("Mystery", 9648),
    ("Romance", 10749),
    ("Science Fiction", 878),
    ("Thriller", 53),
    ("War", 10752),
    ("Western", 37),
]

TV_GENRES = [
    ("Action & Adventure", 10759),
    ("Animation", 16),
    ("Comedy", 35),
    ("Crime", 80),
    ("Documentary", 99),
    ("Drama", 18),
    ("Family", 10751),
    ("Kids", 10762),
    ("Mystery", 9648),
    ("Reality", 10764),
    ("Sci-Fi & Fantasy", 10765),
    ("War & Politics", 10768),
    ("Western", 37),
]
