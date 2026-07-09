"""
build_imdb_cache.py — Erstellt tmdb_cache.json aus IMDB-Bulk-Datasets.
Einmal lokal ausführen; das Ergebnis (tmdb_cache.json) ins Repo committen.

Benötigte Dateien (von https://developer.imdb.com/non-commercial-datasets/):
  title.basics.tsv.gz   (~70 MB)
  title.ratings.tsv.gz  (~3 MB)
  title.crew.tsv.gz     (~20 MB)
  name.basics.tsv.gz    (~200 MB)

Verwendung:
  # Dateien im selben Ordner wie dieses Skript:
  python build_imdb_cache.py

  # Dateien in einem anderen Ordner:
  python build_imdb_cache.py --imdb-dir C:/Downloads/imdb
"""

import argparse
import json
import os
import re
import unicodedata
import pandas as pd

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
    'Film-Noir':   'Film Noir',
    'History':     'Geschichte',
    'Horror':      'Horror',
    'Music':       'Musikfilm',
    'Musical':     'Musical',
    'Mystery':     'Mystery',
    'News':        'Dokumentarfilm',
    'Reality-TV':  'Reality',
    'Romance':     'Liebesfilm',
    'Sci-Fi':      'Science-Fiction',
    'Short':       'Kurzfilm',
    'Sport':       'Sportfilm',
    'Talk-Show':   'Talkshow',
    'Thriller':    'Thriller',
    'War':         'Kriegsfilm',
    'Western':     'Western',
}


def _normalize(text):
    text = unicodedata.normalize('NFKD', str(text))
    text = text.encode('ascii', 'ignore').decode()
    text = re.sub(r'[^a-z0-9 ]', '', text.lower())
    return re.sub(r'\s+', ' ', text).strip()


def _cache_key(title, year):
    y = 0
    try:
        if str(year) not in ('nan', r'\N', '', 'None'):
            y = int(float(year))
    except (ValueError, TypeError):
        pass
    return f"{_normalize(title)}|{y}"


def main():
    parser = argparse.ArgumentParser(description='IMDB-Bulk-Cache-Builder für ZWPUS')
    parser.add_argument(
        '--imdb-dir', default='.',
        help='Ordner mit den IMDB .tsv.gz Dateien (Standard: aktueller Ordner)'
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    imdb_dir   = args.imdb_dir

    # ── Ziel-Titel aus david/robert CSVs ─────────────────────────
    print('── Lade David- und Robert-Ratings ──────────────────────')
    targets: dict[str, tuple[str, int]] = {}
    for fname in ['david_ratings.csv', 'robert_ratings.csv']:
        fpath = os.path.join(script_dir, fname)
        if not os.path.exists(fpath):
            print(f'  ⚠️  {fname} nicht gefunden, überspringe.')
            continue
        df = pd.read_csv(fpath)
        for _, row in df.iterrows():
            title = str(row.get('title', ''))
            year  = row.get('year', 0)
            if title and title != 'nan':
                key = _cache_key(title, year)
                targets[key] = (title, int(float(year)) if str(year) not in ('nan', '') else 0)
    print(f'  {len(targets)} einzigartige Titel zu matchen.\n')

    # ── title.basics laden ────────────────────────────────────────
    basics_path = os.path.join(imdb_dir, 'title.basics.tsv.gz')
    print(f'── Lade title.basics ({basics_path}) ──────────────────')
    basics = pd.read_csv(
        basics_path, sep='\t', na_values=r'\N',
        usecols=['tconst', 'titleType', 'primaryTitle', 'originalTitle', 'startYear', 'genres'],
        dtype=str, low_memory=False,
    )
    basics = basics[basics['titleType'].isin(['movie', 'tvMovie'])]
    basics['startYear'] = pd.to_numeric(basics['startYear'], errors='coerce').fillna(0).astype(int)
    print(f'  {len(basics):,} Filme nach Typ-Filter.')

    print('  Normalisiere Titel...')
    basics['key_p'] = basics.apply(lambda r: _cache_key(r['primaryTitle'],  r['startYear']), axis=1)
    basics['key_o'] = basics.apply(
        lambda r: _cache_key(r['originalTitle'], r['startYear']) if pd.notna(r['originalTitle']) else '',
        axis=1
    )

    mask    = basics['key_p'].isin(targets) | basics['key_o'].isin(targets)
    matched = basics[mask].copy()
    matched['cache_key'] = matched.apply(
        lambda r: r['key_p'] if r['key_p'] in targets else r['key_o'], axis=1
    )
    matched = matched.drop_duplicates(subset=['cache_key'])
    print(f'  {len(matched):,} / {len(targets):,} Titel gematcht.\n')

    # ── title.ratings laden ───────────────────────────────────────
    ratings_path = os.path.join(imdb_dir, 'title.ratings.tsv.gz')
    print(f'── Lade title.ratings ({ratings_path}) ─────────────────')
    ratings = pd.read_csv(ratings_path, sep='\t', na_values=r'\N',
                          usecols=['tconst', 'averageRating', 'numVotes'], dtype=str)
    ratings['averageRating'] = pd.to_numeric(ratings['averageRating'], errors='coerce')
    ratings['numVotes']      = pd.to_numeric(ratings['numVotes'],      errors='coerce')
    matched = matched.merge(ratings, on='tconst', how='left')
    print(f'  ✓ Ratings gejoined.\n')

    # ── title.crew laden ──────────────────────────────────────────
    crew_path = os.path.join(imdb_dir, 'title.crew.tsv.gz')
    print(f'── Lade title.crew ({crew_path}) ───────────────────────')
    crew = pd.read_csv(crew_path, sep='\t', na_values=r'\N',
                       usecols=['tconst', 'directors'], dtype=str)
    matched = matched.merge(crew, on='tconst', how='left')

    all_nconsts: set[str] = set()
    for d in matched['directors'].dropna():
        all_nconsts.update(d.split(','))
    all_nconsts.discard(r'\N')
    print(f'  {len(all_nconsts):,} einzigartige Regisseur-IDs.\n')

    # ── name.basics laden (nur relevante nconsts) ─────────────────
    names_path = os.path.join(imdb_dir, 'name.basics.tsv.gz')
    print(f'── Lade name.basics ({names_path}) ─────────────────────')
    print('  (groß, dauert ~30 Sekunden...)')
    names = pd.read_csv(names_path, sep='\t', na_values=r'\N',
                        usecols=['nconst', 'primaryName'], dtype=str)
    names    = names[names['nconst'].isin(all_nconsts)]
    name_map = dict(zip(names['nconst'], names['primaryName']))
    print(f'  {len(name_map):,} Regisseur-Namen geladen.\n')

    # ── Cache bauen ───────────────────────────────────────────────
    print('── Cache zusammenbauen ─────────────────────────────────')
    cache_path = os.path.join(script_dir, 'tmdb_cache.json')
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        print(f'  Bestehender Cache: {len(cache):,} Einträge (werden nicht überschrieben).')
    except Exception:
        cache = {}

    added = 0
    for _, row in matched.iterrows():
        key = row['cache_key']
        if key in cache and cache[key]:
            continue

        genres_raw = row.get('genres', '')
        genres_de  = []
        if isinstance(genres_raw, str):
            for g in genres_raw.split(','):
                genres_de.append(IMDB_GENRE_DE.get(g.strip(), g.strip()))

        dirs_raw  = row.get('directors', '')
        dir_names = []
        if isinstance(dirs_raw, str):
            for nc in dirs_raw.split(','):
                nc = nc.strip()
                if nc in name_map:
                    dir_names.append(name_map[nc])

        avg_r = row.get('averageRating')
        votes = row.get('numVotes')

        cache[key] = {
            'tmdb_id':     row.get('tconst'),
            'tmdb_rating': round(float(avg_r), 2) if pd.notna(avg_r) else None,
            'vote_count':  int(votes)              if pd.notna(votes) else None,
            'genres':      ', '.join(sorted(set(genres_de))) if genres_de else None,
            'directors':   ', '.join(dir_names)              if dir_names else None,
        }
        added += 1

    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(cache_path) / 1024
    print(f'\n✅ Fertig!')
    print(f'   {added:,} neue Einträge hinzugefügt ({len(cache):,} gesamt)')
    print(f'   → {cache_path}  ({size_kb:.0f} KB)')
    print(f'\n   Nächster Schritt: tmdb_cache.json ins Repo committen.')
    not_found = len(targets) - added - sum(1 for k in targets if k in cache and cache[k])
    if not_found > 0:
        print(f'   ⚠️  {not_found} Titel nicht in IMDB gefunden (Letterboxd-only Releases o.ä.)')


if __name__ == '__main__':
    main()
