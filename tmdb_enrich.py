"""
TMDB Enrichment — Zwei wie Pech & Schwafel Film Personality Test
================================================================

Reichert einen Letterboxd-Export mit Genres, Regisseuren und Crowd-Rating
aus der TMDB-API an. Ergebnisse werden lokal gecacht (tmdb_cache.json),
damit jeder Film nur einmal abgefragt wird.

Benötigt einen kostenlosen TMDB-API-Key:
  themoviedb.org → Konto erstellen → Einstellungen → API

Verwendung:
  python tmdb_enrich.py ratings.csv          # gibt enriched CSV aus
  python tmdb_enrich.py ratings.csv --stats  # zeigt Cache-Statistiken
"""

import json
import os
import re
import time
import unicodedata
import requests
import pandas as pd

# ── TMDB Genre-ID → Deutsch ───────────────────────────────────────
TMDB_GENRE_MAP = {
    28:    'Action',
    12:    'Abenteuer',
    16:    'Animation',
    35:    'Komödie',
    80:    'Krimi',
    99:    'Dokumentarfilm',
    18:    'Drama',
    10751: 'Familienfilm',
    14:    'Fantasy',
    36:    'Geschichte',
    27:    'Horror',
    10402: 'Musikfilm',
    9648:  'Mystery',
    10749: 'Liebesfilm',
    878:   'Science-Fiction',
    10770: 'Fernsehfilm',
    53:    'Thriller',
    10752: 'Kriegsfilm',
    37:    'Western',
    10759: 'Action',       # Action & Adventure (TV)
    10762: 'Familienfilm', # Kids (TV)
    10763: 'Dokumentarfilm', # News (TV)
    10764: 'Reality',
    10765: 'Science-Fiction',
    10766: 'Drama',        # Soap
    10767: 'Talkshow',
    10768: 'Kriegsfilm',   # War & Politics
}

TMDB_BASE   = 'https://api.themoviedb.org/3'
CACHE_FILE  = 'tmdb_cache.json'
DELAY       = 0.25   # seconds between API requests (polite)
DELAY_WARM  = 1.0    # Langsamere Rate für Background-Warming

# ── IMDB-Genre (EN) → Deutsch ─────────────────────────────────────
IMDB_GENRE_DE = {
    'Action':      'Action',
    'Adventure':   'Abenteuer',
    'Animation':   'Animation',
    'Biography':   'Biografie',
    'Comedy':      'Komödie',
    'Crime':       'Krimi',
    'Documentary': 'Dokumentarfilm',
    'Drama':       'Drama',
    'Family':      'Familienfilm',
    'Fantasy':     'Fantasy',
    'History':     'Geschichte',
    'Horror':      'Horror',
    'Music':       'Musikfilm',
    'Musical':     'Musical',
    'Mystery':     'Mystery',
    'Romance':     'Liebesfilm',
    'Sci-Fi':      'Science-Fiction',
    'Sport':       'Sportfilm',
    'Thriller':    'Thriller',
    'War':         'Kriegsfilm',
    'Western':     'Western',
    # Deutsche IMDB-Namen (für User mit DE-IMDB)
    'Abenteuer':   'Abenteuer',
    'Biografie':   'Biografie',
    'Komödie':     'Komödie',
    'Krimi':       'Krimi',
    'Dokumentarfilm': 'Dokumentarfilm',
    'Familienfilm': 'Familienfilm',
    'Geschichte':  'Geschichte',
    'Kriegsfilm':  'Kriegsfilm',
    'Liebesfilm':  'Liebesfilm',
    'Musikfilm':   'Musikfilm',
    'Science-Fiction': 'Science-Fiction',
    'Sportfilm':   'Sportfilm',
}


# ── Hilfsfunktionen ───────────────────────────────────────────────

def _normalize(text):
    """Titel normalisieren für Cache-Keys und Fuzzy-Matching."""
    text = unicodedata.normalize('NFKD', str(text))
    text = text.encode('ascii', 'ignore').decode()
    text = re.sub(r'[^a-z0-9 ]', '', text.lower())
    return re.sub(r'\s+', ' ', text).strip()


def _cache_key(title, year):
    return f"{_normalize(title)}|{int(year) if year and str(year) != 'nan' else '0'}"


def load_cache(path=CACHE_FILE):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
        except Exception:
            return {}
    return {}


def save_cache(cache, path=CACHE_FILE):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # Read-only filesystem (z.B. Streamlit Cloud) — kein Cache-Schreiben, aber Daten im RAM


def populate_cache_from_imdb(df, cache_path=CACHE_FILE):
    """
    Befüllt den TMDB-Cache mit Daten aus einem IMDB-Export.
    Spart TMDB-API-Calls für LB-User die die gleichen Filme haben.

    df braucht: title, year, imdb_rating, num_votes, genres, directors
    """
    cache = load_cache(cache_path)
    added = 0

    for _, row in df.iterrows():
        title = row.get('title', '')
        year  = row.get('year')
        if not title or pd.isna(title):
            continue

        key = _cache_key(title, year)
        if key in cache and cache[key]:  # nur wenn noch nicht gecacht
            continue

        # Genres: IMDB-EN → Deutsch
        genres_raw = row.get('genres', '')
        genres_de  = []
        if isinstance(genres_raw, str):
            for g in [x.strip() for x in genres_raw.split(',')]:
                genres_de.append(IMDB_GENRE_DE.get(g, g))

        # Directors
        dirs_raw = row.get('directors', '')
        dirs = dirs_raw if isinstance(dirs_raw, str) else ''

        imdb_r   = row.get('imdb_rating')
        num_v    = row.get('num_votes')

        cache[key] = {
            'tmdb_id':     None,  # IMDB-Const ≠ TMDB-ID
            'tmdb_rating': round(float(imdb_r), 2) if pd.notna(imdb_r) else None,
            'vote_count':  int(num_v) if pd.notna(num_v) else None,
            'genres':      ', '.join(sorted(set(genres_de))) if genres_de else None,
            'directors':   dirs if dirs else None,
        }
        added += 1

    if added > 0:
        save_cache(cache, cache_path)
        print(f'  Cache: {added} Filme aus IMDB-Export hinzugefügt (gesamt: {len(cache)})')

    return added


# ── TMDB API ──────────────────────────────────────────────────────

def _search_movie(title, year, api_key, session):
    """Sucht einen Film auf TMDB. Gibt das beste Ergebnis zurück oder None."""
    params = {'api_key': api_key, 'query': title, 'language': 'en-US'}
    if year and str(year) not in ('nan', '0', ''):
        params['year'] = int(year)

    for _attempt in range(3):
        try:
            r = session.get(f'{TMDB_BASE}/search/movie', params=params, timeout=10)
            if r.status_code == 429:
                time.sleep(2 ** _attempt)  # 1s, 2s, 4s backoff
                continue
            r.raise_for_status()
            results = r.json().get('results', [])
            break
        except Exception as _e:
            if _attempt == 2:
                return None
            time.sleep(1)
    else:
        return None

    if not results:
        # Retry without year constraint
        params.pop('year', None)
        try:
            r = session.get(f'{TMDB_BASE}/search/movie', params=params, timeout=10)
            results = r.json().get('results', [])
        except Exception as _e2:
            print(f'  TMDB retry FEHLER [{title}]: {_e2}')
            return None

    if not results:
        return None

    # Pick best match: exact title + closest year
    title_norm = _normalize(title)
    year_int   = int(year) if year and str(year) not in ('nan', '0') else None

    def score(res):
        r_year = int(res.get('release_date', '0000')[:4] or 0)
        year_diff = abs(r_year - year_int) if year_int else 5
        title_match = _normalize(res.get('title', '')) == title_norm or \
                      _normalize(res.get('original_title', '')) == title_norm
        return (not title_match, year_diff, -res.get('popularity', 0))

    return sorted(results, key=score)[0]


def _get_credits(movie_id, api_key, session):
    """Holt Regisseure für eine TMDB-Film-ID."""
    try:
        r = session.get(f'{TMDB_BASE}/movie/{movie_id}/credits',
                        params={'api_key': api_key}, timeout=10)
        r.raise_for_status()
        crew = r.json().get('crew', [])
        directors = [c['name'] for c in crew if c.get('job') == 'Director']
        return directors
    except Exception:
        return []


def enrich_one(title, year, api_key, session, api_delay=None):
    """
    Fragt TMDB für einen Film ab.
    Gibt Dict zurück: {tmdb_id, tmdb_rating, vote_count, genres, directors}
    oder None bei Fehler.
    """
    result = _search_movie(title, year, api_key, session)
    if not result:
        return None

    movie_id   = result['id']
    tmdb_rating = result.get('vote_average')
    vote_count  = result.get('vote_count', 0)
    genre_ids   = result.get('genre_ids', [])
    genres      = [TMDB_GENRE_MAP[g] for g in genre_ids if g in TMDB_GENRE_MAP]

    time.sleep(api_delay if api_delay is not None else DELAY)
    directors = _get_credits(movie_id, api_key, session)

    return {
        'tmdb_id':     movie_id,
        'tmdb_rating': round(tmdb_rating, 2) if tmdb_rating else None,
        'vote_count':  vote_count,
        'genres':      ', '.join(sorted(set(genres))),
        'directors':   ', '.join(directors),
    }


# ── Hauptfunktion ─────────────────────────────────────────────────

def enrich_letterboxd(df, api_key, cache_path=CACHE_FILE, progress_cb=None, api_delay=None):
    """
    Reichert einen Letterboxd-DataFrame mit TMDB-Daten an.

    df muss Spalten haben: title, year  (aus load_letterboxd_export)
    Gibt df zurück mit zusätzlichen Spalten:
      tmdb_rating, vote_count, genres, directors

    progress_cb: optionaler Callback(done, total) für Fortschrittsanzeige
                 (z.B. Streamlit progress bar)
    """
    cache   = load_cache(cache_path)
    session = requests.Session()
    session.headers.update({'User-Agent': 'ZWPUS-FilmPersonality/1.0'})

    new_entries = 0
    total = len(df)
    n_uncached = sum(
        1 for _, row in df.iterrows()
        if _cache_key(row.get('title', ''), row.get('year', 0)) not in cache
    )
    print(f'  enrich_letterboxd: {total} Filme, {n_uncached} nicht im Cache, api_key={api_key[:8]}...')

    for i, (idx, row) in enumerate(df.iterrows()):
        key = _cache_key(row.get('title', ''), row.get('year', 0))

        if key not in cache:
            data = enrich_one(row.get('title', ''), row.get('year'), api_key, session, api_delay=api_delay)
            cache[key] = data or {}
            new_entries += 1
            if new_entries % 20 == 0:
                save_cache(cache, cache_path)
            # progress_cb nur bei echten API-Calls → kein DOM-Spam für Cache-Hits
            if progress_cb:
                progress_cb(new_entries, n_uncached)
            if new_entries % 5 == 0:
                time.sleep(0.1)  # kurze Pause gegen Rate-Limiting

    if new_entries > 0:
        save_cache(cache, cache_path)  # silent on error
        print(f'  TMDB: {new_entries} neue Einträge verarbeitet (gesamt im RAM: {len(cache)})')

    # Einmal am Ende signalisieren: done — schließt die Progress-Bar
    if progress_cb:
        progress_cb(total, total)

    # Merge cache into df
    def get_field(row, field):
        key  = _cache_key(row.get('title', ''), row.get('year', 0))
        data = cache.get(key) or {}
        return data.get(field)

    df = df.copy()
    df['tmdb_rating'] = df.apply(lambda r: get_field(r, 'tmdb_rating'), axis=1)
    df['vote_count']  = df.apply(lambda r: get_field(r, 'vote_count'),  axis=1)
    df['genres']      = df.apply(lambda r: get_field(r, 'genres'),      axis=1)
    df['directors']   = df.apply(lambda r: get_field(r, 'directors'),   axis=1)

    return df


def cache_stats(cache_path=CACHE_FILE):
    """Zeigt Statistiken über den lokalen Cache."""
    cache = load_cache(cache_path)
    found     = sum(1 for v in cache.values() if v)
    not_found = sum(1 for v in cache.values() if not v)
    with_genre = sum(1 for v in cache.values() if v and v.get('genres'))
    with_dir   = sum(1 for v in cache.values() if v and v.get('directors'))
    print(f'Cache: {len(cache)} Einträge total')
    print(f'  Gefunden:      {found}')
    print(f'  Nicht gefunden:{not_found}')
    print(f'  Mit Genres:    {with_genre}')
    print(f'  Mit Regisseur: {with_dir}')


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    args = sys.argv[1:]

    if '--stats' in args:
        cache_stats()
        sys.exit(0)

    if not args:
        print('Verwendung: python tmdb_enrich.py ratings.csv [--stats]')
        sys.exit(0)

    csv_path = args[0]
    api_key  = os.environ.get('TMDB_API_KEY', '')
    if not api_key:
        api_key = input('TMDB API-Key: ').strip()

    from film_personality import load_letterboxd_export
    df, _ = load_letterboxd_export(csv_path)
    print(f'Anreichere {len(df)} Filme...')

    def progress(done, total):
        if done % 50 == 0 or done == total:
            print(f'  {done}/{total}', end='\r')

    df_enriched = enrich_letterboxd(df, api_key, progress_cb=progress)
    out = csv_path.replace('.csv', '_enriched.csv')
    df_enriched.to_csv(out, index=False, encoding='utf-8')
    print(f'\nGespeichert: {out}')
    cache_stats()
