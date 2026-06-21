"""
Letterboxd Daten-Import — Zwei wie Pech und Schwafel
=====================================================

EMPFOHLENER WEG: Letterboxd Daten-Export (kein Scraping nötig!)
----------------------------------------------------------------
David und Robert können ihre Daten direkt von Letterboxd exportieren:
  1. letterboxd.com/settings/data/  →  "Export Your Data"
  2. ZIP entpacken → ratings.csv liegt darin
  3. Dateien umbenennen und hier ablegen:
       behaind_export.csv        (David)
       robsntown_export.csv      (Robert, bevorzugter Account)
       roberthofmannio_export.csv (Robert, älterer Account — optional)
  4. python scrape_letterboxd.py
     → erzeugt david_ratings.csv + robert_ratings.csv

Export-Format von Letterboxd:
  Date, Name, Year, Letterboxd URI, Rating
  (Rating: 0.5–5 Sterne)

FALLBACK: Web-Scraping (weniger zuverlässig — Letterboxd blockt oft)
---------------------------------------------------------------------
Falls kein Export verfügbar: python scrape_letterboxd.py --scrape
  pip install requests beautifulsoup4

Ausgabe-CSVs:
  david_ratings.csv  — alle Bewertungen von David
  robert_ratings.csv — alle Bewertungen von Robert (beide Accounts gemergt)

Letterboxd Rating-Skala: 0.5–5 Sterne (×2 = 1–10 für IMDB-Vergleich)
"""

import time
import re
import sys
import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────────────
# KONFIGURATION
# ─────────────────────────────────────────────────────────────────

PROFILES = {
    'david':        ['behaind'],
    'robert_new':   ['robsntown'],           # bevorzugt (eigener Account)
    'robert_old':   ['roberthofmannio'],     # Fallback (älteres Fan-Archiv)
}

DELAY_BETWEEN_PAGES = 1.5   # Sekunden zwischen Requests (höflich bleiben)
MAX_PAGES           = 999   # Sicherheits-Limit
OUT_DIR             = os.path.dirname(os.path.abspath(__file__))

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-User': '?1',
}

# Globale Session — behält Cookies über alle Requests hinweg
_SESSION = None

def _get_session():
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(HEADERS)
        # Letterboxd-Homepage kurz besuchen damit Cookies gesetzt werden
        try:
            _SESSION.get('https://letterboxd.com/', timeout=20)
            time.sleep(1.0)
        except Exception:
            pass
    return _SESSION


# ─────────────────────────────────────────────────────────────────
# SCRAPING
# ─────────────────────────────────────────────────────────────────

def get_page(username, page_num):
    """Lädt eine Seite der Letterboxd-Ratings. Gibt (soup, ok) zurück."""
    url = f'https://letterboxd.com/{username}/films/ratings/page/{page_num}/'
    referer = (
        f'https://letterboxd.com/{username}/films/ratings/'
        if page_num == 1
        else f'https://letterboxd.com/{username}/films/ratings/page/{page_num - 1}/'
    )
    try:
        session = _get_session()
        r = session.get(url, headers={'Referer': referer}, timeout=20)
        if r.status_code == 403:
            # Kurze Pause und einmal retry
            print(f'    HTTP 403 — warte 4s und versuche nochmal ...')
            time.sleep(4)
            r = session.get(url, headers={'Referer': referer}, timeout=20)
        if r.status_code == 404:
            return None, False
        if r.status_code != 200:
            print(f'    Warnung: HTTP {r.status_code} für {url}')
            return None, False
        return BeautifulSoup(r.text, 'html.parser'), True
    except Exception as e:
        print(f'    Fehler beim Laden von {url}: {e}')
        return None, False


def get_total_pages(soup):
    """Liest die Gesamtseitenanzahl aus der Pagination."""
    # Letterboxd zeigt z.B. "1 2 3 … 42" — wir wollen die letzte Zahl
    last = soup.select('li.paginate-page a')
    if not last:
        return 1
    try:
        return int(last[-1].text.strip())
    except ValueError:
        return 1


def parse_films_from_page(soup):
    """
    Extrahiert alle Filme von einer Seite.
    Gibt Liste von Dicts: {slug, title, year, rating_raw, rating}
    rating_raw: 1–10 (Letterboxd intern)
    rating:     0.5–5 Sterne (Letterboxd-Skala)
    """
    films = []
    for li in soup.select('li.poster-container'):
        # Rating aus CSS-Klasse (rated-1 bis rated-10)
        rating_raw = None
        for cls in li.get('class', []):
            m = re.match(r'rated-(\d+)', cls)
            if m:
                rating_raw = int(m.group(1))
                break

        # Manche Einträge sind ohne Rating (geloggt aber nicht bewertet) — überspringen
        if rating_raw is None:
            continue

        div = li.find('div', class_='film-poster')
        if div is None:
            continue

        slug  = div.get('data-film-slug', '')
        title = div.get('data-film-name', '')
        year  = div.get('data-film-year', '')

        try:
            year = int(year) if year else None
        except ValueError:
            year = None

        films.append({
            'slug':       slug,
            'title':      title,
            'year':       year,
            'rating_raw': rating_raw,
            'rating':     rating_raw / 2,   # 0.5–5
        })
    return films


def scrape_profile(username):
    """
    Scrapt alle Seiten eines Letterboxd-Profils.
    Gibt DataFrame zurück.
    """
    global _SESSION
    # Frische Session pro Profil (neue Cookies)
    _SESSION = None

    print(f'\n  Scraping @{username} ...')

    # Seite 1 laden und Gesamtseiten ermitteln
    soup, ok = get_page(username, 1)
    if not ok or soup is None:
        print(f'  → Profil @{username} nicht erreichbar.')
        return pd.DataFrame()

    total = get_total_pages(soup)
    print(f'  Seiten gesamt: {total}')

    all_films = parse_films_from_page(soup)
    print(f'  Seite  1/{total}  — {len(all_films)} Filme bisher', end='\r')

    for page in range(2, min(total + 1, MAX_PAGES + 1)):
        time.sleep(DELAY_BETWEEN_PAGES)
        soup, ok = get_page(username, page)
        if not ok or soup is None:
            print(f'\n  Seite {page} fehlgeschlagen — stoppe.')
            break
        films = parse_films_from_page(soup)
        all_films.extend(films)
        print(f'  Seite {page:2d}/{total}  — {len(all_films)} Filme bisher', end='\r')

    print()  # Newline nach \r

    df = pd.DataFrame(all_films)
    df['username'] = username
    print(f'  ✓ {len(df)} Bewertungen von @{username} geladen.')
    return df


# ─────────────────────────────────────────────────────────────────
# MERGE
# ─────────────────────────────────────────────────────────────────

def merge_robert(df_new, df_old):
    """
    Merged Roberts zwei Accounts.
    Für jeden Film: robsntown (neu) bevorzugt, roberthofmannio (alt) als Fallback.
    Duplikate (gleicher slug): neuerer Account hat Vorrang.
    """
    if df_new.empty and df_old.empty:
        return pd.DataFrame()
    if df_new.empty:
        print('  Warnung: robsntown leer — verwende nur roberthofmannio')
        return df_old.copy()
    if df_old.empty:
        return df_new.copy()

    # slugs die in robsntown vorhanden sind
    new_slugs = set(df_new['slug'])

    # Aus dem alten Account nur Filme nehmen die im neuen NICHT vorkommen
    df_old_only = df_old[~df_old['slug'].isin(new_slugs)].copy()
    df_old_only['source'] = 'roberthofmannio'

    df_new = df_new.copy()
    df_new['source'] = 'robsntown'

    merged = pd.concat([df_new, df_old_only], ignore_index=True)
    merged = merged.drop_duplicates(subset='slug', keep='first')

    n_new  = len(df_new)
    n_added = len(df_old_only)
    print(f'  Robert-Merge: {n_new} aus robsntown + {n_added} nur aus roberthofmannio = {len(merged)} gesamt')
    return merged


# ─────────────────────────────────────────────────────────────────
# DATA EXPLORATION
# ─────────────────────────────────────────────────────────────────

def explore(df, name):
    """
    Gibt einen kompakten Überblick über die gescrapten Daten.
    Prüft auf typische Probleme.
    """
    sep = '─' * 55
    print(f'\n{sep}')
    print(f'  DATA EXPLORATION: {name.upper()}')
    print(sep)

    print(f'  Einträge gesamt:   {len(df)}')
    print(f'  Mit Jahr:          {df["year"].notna().sum()}  ({df["year"].notna().mean()*100:.1f}%)')
    print(f'  Mit Titel:         {(df["title"] != "").sum()}')

    # Rating-Verteilung
    print(f'\n  Rating-Verteilung (0.5–5 Sterne):')
    for r in sorted(df['rating'].unique()):
        n = (df['rating'] == r).sum()
        bar = '█' * int(n / len(df) * 40)
        stars = '★' * int(r) + ('½' if r % 1 else '')
        print(f'    {stars:8s} ({r:.1f})  {bar}  {n}')

    # Jahres-Abdeckung
    if df['year'].notna().sum() > 0:
        years = df['year'].dropna()
        print(f'\n  Jahres-Abdeckung:  {int(years.min())}–{int(years.max())}')
        print(f'  Median-Jahr:       {int(years.median())}')

        by_decade = df.groupby((df['year'] // 10 * 10).fillna(0).astype(int))['rating'].agg(['count','mean'])
        print(f'\n  Bewertungen pro Jahrzehnt:')
        for dec, row in by_decade.iterrows():
            if dec == 0: continue
            bar = '█' * int(row['count'] / len(df) * 30)
            print(f'    {dec}er  {bar}  n={int(row["count"])}  Ø={row["mean"]:.2f}')

    # Mögliche Probleme
    print(f'\n  Mögliche Datenproblem-Checks:')
    dup = df.duplicated(subset='slug').sum()
    print(f'    Duplikate (gleicher slug):  {dup}', '⚠️' if dup > 0 else '✓')
    no_year = df['year'].isna().sum()
    print(f'    Ohne Jahr:                  {no_year}', '⚠️' if no_year > 10 else '✓')
    no_title = (df['title'] == '').sum()
    print(f'    Ohne Titel:                 {no_title}', '⚠️' if no_title > 0 else '✓')
    out_of_range = ((df['rating'] < 0.5) | (df['rating'] > 5)).sum()
    print(f'    Ratings außerhalb 0.5–5:    {out_of_range}', '⚠️' if out_of_range > 0 else '✓')

    print(sep)


# ─────────────────────────────────────────────────────────────────
# LETTERBOXD EXPORT PARSER
# ─────────────────────────────────────────────────────────────────

def parse_lb_export(path, username_label):
    """
    Liest eine Letterboxd-Export-CSV ein.

    Format: Date, Name, Year, Letterboxd URI, Rating
    Gibt DataFrame mit Spalten: slug, title, year, rating_raw, rating, username
    """
    df = pd.read_csv(path, encoding='utf-8')
    df.columns = [c.strip() for c in df.columns]

    # Pflichtfelder prüfen
    needed = {'Name', 'Rating'}
    if not needed.issubset(set(df.columns)):
        raise ValueError(f"Export-CSV hat nicht die erwarteten Spalten. Gefunden: {list(df.columns)}")

    df = df[df['Rating'].notna()].copy()
    df['rating'] = pd.to_numeric(df['Rating'], errors='coerce')
    df = df[df['rating'].notna() & (df['rating'] > 0)]

    # Slug aus URI extrahieren (https://boxd.it/XXXX oder /film/slug/)
    if 'Letterboxd URI' in df.columns:
        df['slug'] = df['Letterboxd URI'].str.extract(r'boxd\.it/([^/\s]+)', expand=False).fillna('')
    else:
        df['slug'] = ''

    df['title'] = df['Name'].fillna('')
    df['year']  = pd.to_numeric(df.get('Year', pd.Series(dtype=float)), errors='coerce')
    df['rating_raw'] = (df['rating'] * 2).round().astype(int)
    df['username'] = username_label

    result = df[['slug', 'title', 'year', 'rating_raw', 'rating', 'username']].reset_index(drop=True)
    print(f'  ✓ {len(result)} Bewertungen aus {os.path.basename(path)} geladen.')
    return result


# ─────────────────────────────────────────────────────────────────
# HAUPTPROGRAMM
# ─────────────────────────────────────────────────────────────────


# -----------------------------------------------------------------
# LETTERBOXD EXPORT PARSER
# -----------------------------------------------------------------

def parse_lb_export(path, username_label):
    """
    Liest eine Letterboxd-Export-CSV ein.
    Format: Date, Name, Year, Letterboxd URI, Rating
    """
    df = pd.read_csv(path, encoding='utf-8')
    df.columns = [c.strip() for c in df.columns]

    needed = {'Name', 'Rating'}
    if not needed.issubset(set(df.columns)):
        raise ValueError(
            f"Unerwartete Spalten in {path}. Gefunden: {list(df.columns)}\n"
            "Erwartet: Date, Name, Year, Letterboxd URI, Rating"
        )

    df = df[df['Rating'].notna()].copy()
    df['rating'] = pd.to_numeric(df['Rating'], errors='coerce')
    df = df[df['rating'].notna() & (df['rating'] > 0)]

    if 'Letterboxd URI' in df.columns:
        df['slug'] = df['Letterboxd URI'].str.extract(
            r'boxd\.it/([^/\s]+)', expand=False).fillna('')
    else:
        df['slug'] = ''

    df['title'] = df['Name'].fillna('')
    df['year'] = pd.to_numeric(
        df['Year'] if 'Year' in df.columns else pd.Series(dtype=float),
        errors='coerce')
    df['rating_raw'] = (df['rating'] * 2).round().astype(int)
    df['username'] = username_label

    result = df[['slug', 'title', 'year', 'rating_raw', 'rating',
                 'username']].reset_index(drop=True)
    print(f'  OK {len(result)} Bewertungen aus {os.path.basename(path)} geladen.')
    return result


# -----------------------------------------------------------------
# HAUPTPROGRAMM
# -----------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--scrape', action='store_true',
                    help='Web-Scraping statt Export-CSVs')
    args = ap.parse_args()

    print('=' * 55)
    print('  Letterboxd Import - Zwei wie Pech und Schwafel')
    print('=' * 55)

    david_exp   = os.path.join(OUT_DIR, 'behaind_export.csv')
    rob_new_exp = os.path.join(OUT_DIR, 'robsntown_export.csv')
    rob_old_exp = os.path.join(OUT_DIR, 'roberthofmannio_export.csv')

    has_exports = (os.path.exists(david_exp) or
                   os.path.exists(rob_new_exp) or
                   os.path.exists(rob_old_exp))
    use_export = (not args.scrape) and has_exports

    if use_export:
        print('\nModus: Letterboxd-Export-CSVs\n')

        print('[1/3] David Hain (@behaind)')
        if os.path.exists(david_exp):
            df_david = parse_lb_export(david_exp, 'behaind')
        else:
            print('  Warnung: behaind_export.csv nicht gefunden.')
            df_david = pd.DataFrame()

        print('\n[2/3] Robert Hoffmann (@robsntown + @roberthofmannio)')
        df_rob_new = pd.DataFrame()
        df_rob_old = pd.DataFrame()
        if os.path.exists(rob_new_exp):
            df_rob_new = parse_lb_export(rob_new_exp, 'robsntown')
        else:
            print('  Warnung: robsntown_export.csv nicht gefunden.')
        if os.path.exists(rob_old_exp):
            df_rob_old = parse_lb_export(rob_old_exp, 'roberthofmannio')

        print('\n[3/3] Merge und Speichern ...')
        df_robert = merge_robert(df_rob_new, df_rob_old)

    else:
        if not args.scrape:
            print('\nKeine Export-CSVs gefunden - versuche Web-Scraping.')
            print('Tipp: Besser letterboxd.com/settings/data/ nutzen!\n')
        else:
            print('\nModus: Web-Scraping\n')

        print('[1/3] David Hain (@behaind)')
        df_david = scrape_profile('behaind')

        print('\n[2/3] Robert Hoffmann (@robsntown + @roberthofmannio)')
        df_rob_new = scrape_profile('robsntown')
        df_rob_old = scrape_profile('roberthofmannio')

        print('\n[3/3] Merge und Speichern ...')
        df_robert = merge_robert(df_rob_new, df_rob_old)

    # Speichern
    if not df_david.empty:
        out = os.path.join(OUT_DIR, 'david_ratings.csv')
        df_david.to_csv(out, index=False, encoding='utf-8')
        print(f'  -> david_ratings.csv  ({len(df_david)} Eintraege)')

    if not df_robert.empty:
        out = os.path.join(OUT_DIR, 'robert_ratings.csv')
        df_robert.to_csv(out, index=False, encoding='utf-8')
        print(f'  -> robert_ratings.csv ({len(df_robert)} Eintraege)')

    # Data Exploration
    print('\n' + '=' * 55)
    print('  DATA EXPLORATION')
    print('=' * 55)

    if not df_david.empty:
        explore(df_david, 'David (behaind)')
    if not df_robert.empty:
        explore(df_robert, 'Robert (robsntown + roberthofmannio)')

    if not df_david.empty and not df_robert.empty:
        overlap = set(df_david['slug']) & set(df_robert['slug'])
        print(f'\n  Ueberschneidung David u. Robert: {len(overlap)} Filme')
        print(f'  Nur David:  {len(set(df_david["slug"]) - set(df_robert["slug"]))}')
        print(f'  Nur Robert: {len(set(df_robert["slug"]) - set(df_david["slug"]))}')
        merged = df_david[['slug', 'rating']].merge(
            df_robert[['slug', 'rating']], on='slug',
            suffixes=('_david', '_robert'))
        if len(merged) >= 10:
            r = merged['rating_david'].corr(merged['rating_robert'])
            print(f'  Pearson-Korrelation David-Robert: r={r:.3f}  (n={len(merged)})')

    print('\nFertig. Naechster Schritt:')
    print('  python film_personality.py deine_ratings.csv [geburtsjahr] [name]')
    print('  (david_ratings.csv + robert_ratings.csv werden automatisch erkannt)')


if __name__ == '__main__':
    main()
