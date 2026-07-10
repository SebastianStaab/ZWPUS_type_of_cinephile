"""
Film Personality Test — Zwei wie Pech und Schwafel Community
=============================================================

Berechnet ein Persönlichkeitsprofil aus einem IMDB-Export (CSV).

Verwendung:
    python film_personality.py ratings.csv [geburtsjahr] [name]

Beispiele:
    python film_personality.py ratings.csv 1996 Seb
    python film_personality.py ratings.csv          # fragt nach Eingaben

IMDB-Export herunterladen: imdb.com → Dein Account → Listen → Deine Bewertungen → Export

Hinweis Letterboxd: Letterboxd-Exporte haben keine Genres/Regisseure.
    Genre- und Regisseur-Analysen benötigen dann TMDB-API-Anreicherung (TODO).
    Correlation-Dimensionen und Bonus-Achievements funktionieren aber auch mit LB-Export.
"""

import sys, os, math, warnings, unicodedata
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
from collections import Counter

# ─────────────────────────────────────────────────────────────────
# KONFIGURATION — hier anpassen
# ─────────────────────────────────────────────────────────────────

# Persoehnlichkeitsdimensionen
DIM_STRENG_THRESHOLD   = -0.5   # Ø(user - imdb) < X → Streng
DIM_MILD_THRESHOLD     =  0.5   # Ø(user - imdb) > X → Mild
DIM_POLAR_THRESHOLD    =  5.0   # MSE(user−IMDB) > X → Polarisierer  (≈ σ 2.2²)
DIM_DIPLO_THRESHOLD    =  1.5   # MSE(user−IMDB) < X → Diplomat     (≈ σ 1.2²)
DIM_SPEZIALIST_ENTROPY = 0.70   # normierte Genre-Entropie < X → Spezialist
DIM_OMNIVORE_ENTROPY   = 0.88   # normierte Genre-Entropie > X → Omnivore
DIM_KLASSIKER_YEAR     = 1995   # Median-Jahr < X → Klassiker
DIM_ZEITGEIST_YEAR     = 2010   # Median-Jahr > X → Zeitgeist

# Genre-Achievements
# Trigger: user_avg_genre - user_gesamt_avg > X  (relativ zum eigenen Schnitt,
# d.h. strenge Bewerter werden nicht benachteiligt — nur relativ bessere Genres zählen)
GENRE_DIFF_THRESHOLD = 0.2   # Differenz auf 1-10 Skala
GENRE_MIN_FILMS      = 3     # Mind. X Filme im Genre

# Bonus-Achievements
MAINSTREAMER_CORR     = 0.60
REBELL_CORR           = 0.00
BLOCKBUSTER_VOTES     = 500_000  # Min. Votes → als Blockbuster (IMDB-Daten)
ARTHOUSE_VOTES        = 50_000   # Max. Votes → als Arthouse   (IMDB-Daten)
BLOCKBUSTER_VOTES_TMDB = 15_000  # Min. Votes für TMDB-Daten (Avengers ~30K, La La Land ~18K)
ARTHOUSE_VOTES_TMDB    = 3_000   # Max. Votes für TMDB-Daten (Portrait of a Lady ~4K, First Reformed ~2K)
BLOCKBUSTER_MIN_FILMS  = 10
BLOCKBUSTER_MIN_FILMS_TMDB = 5   # Weniger Filme qualifizieren sich bei TMDB-Schwellen
ARTHOUSE_MIN_FILMS    = 5
ARTHOUSE_MIN_FILMS_TMDB = 3      # Arthouse-Filme sind seltener in kleinen Sammlungen
BLOCKBUSTER_DIFF      = 0.5      # user_avg_blockbuster - gesamt_avg > X
ARTHOUSE_DIFF         = 0.5

HIDDEN_GEM_IMDB_MAX   = 6.5      # IMDB-Schnitt des Films < X
HIDDEN_GEM_USER_MIN   = 9.0      # User-Bewertung ≥ X
HIDDEN_GEM_MIN        = 5        # Mind. X solcher Filme

TRASH_KING_IMDB_MAX   = 5.0      # IMDB-Schnitt < X (objektiver Trash)
TRASH_KING_USER_MIN   = 8.0      # User liebte ihn trotzdem (≥ X)
TRASH_KING_MIN        = 5        # Mind. X solcher Filme

NOSTALGIE_DIFF        = 0.5      # formativ - nicht-formativ > X → Nostalgiker
ANTI_NOSTALGIE_DIFF   = -0.5     # formativ - nicht-formativ < X → Anti-Nostalgiker
NOSTALGIE_MIN_FILMS   = 5        # Mind. X Filme pro Gruppe

# Tony Surroundi (Insider)
# Trigger: User hat "Zwei wie Pech und Schwafel" auf IMDB bewertet
# (echter Community-Insider-Check — wird im Roh-Export vor dem Film-Filter geprüft)
TONY_SHOW_TITLES      = ['zwei wie pech schwafel', 'zwei wie pech und schwafel']  # Kleingeschrieben für Matching

# Progressive Achievements — Milestones für Anzahl bewerteter Filme/Serien
FILM_MILESTONES   = [
    (50,   '🎟️',  'Kinogeher',       'Du hast schon 50 Filme bewertet. Gut warm.'),
    (100,  '🎬',  'Filmfan',          '100 Filme — du bist offiziell ein Filmfan.'),
    (250,  '🍿',  'Popcorn-Profi',    '250 Filme. Du hast mehr gesehen als die meisten.'),
    (500,  '🎥',  'Cinephile',        '500 Filme. Kino ist nicht dein Hobby, es ist dein Lifestyle.'),
    (1000, '🏆',  'Filmmillionär',    '1000 Filme. Unnachahmlich. Bitte heirate uns.'),
]
SERIES_MILESTONES = [
    (20,   '📺',  'Serienstarter',    '20 Serien — du weißt was ein Cliffhanger ist.'),
    (50,   '🛋️',  'Couch-Experte',    '50 Serien. Deine Couch kennt dich besser als deine Familie.'),
    (100,  '📡',  'Serienjunkie',     '100 Serien bewertet. Kein Spoiler bringt dich aus der Ruhe.'),
    (250,  '🌀',  'Binge-Lord',       '250 Serien. Du hast "Sehen was als nächstes" zu deinem Beruf gemacht.'),
    (1000, '👑',  'Serienmillionär',  '1000 Serien. Wir haben keine Worte. Respekt.'),
]

# ─────────────────────────────────────────────────────────────────
# GENRE-ACHIEVEMENT-DEFINITIONEN (deutsche Namen aus IMDB-Export)
# Format: 'IMDB-Genre': (emoji, achievement_name, kurzbeschreibung)
# ─────────────────────────────────────────────────────────────────
GENRE_ACHIEVEMENTS = {
    'Action':         ('🥋', 'Karate-Tiger',                    'Explosivstoffe und Fäuste — du liebst es.'),
    'Abenteuer':      ('🤠', 'Indiana Jones',                    'Auf ins Abenteuer. Hut aufsetzen nicht vergessen.'),
    'Animation':      ('🧸', 'The superior way of telling Fantasy', 'Gezeichnet, animiert oder gerendert — für dich ist das Leinwand auf Augenhöhe mit Realfilm.'),
    'Biografie':      ('📖', 'True Story',                       '"Basiert auf wahren Begebenheiten" reicht dir als Kaufargument.'),
    'Dokumentarfilm': ('📹', 'Doku Dealer',                      'Arte läuft bei dir rund um die Uhr.'),
    'Drama':          ('😭', 'Drama Queen',                      'Du leidest gerne. Cineastisch gesehen.'),
    'Familienfilm':   ('👨‍👩‍👧', 'Cozy up',                       'Familienabend-Architekt. Popcorn ist Pflicht.'),
    'Fantasy':        ('🧙', 'Dragons?',                         'Du bist zu jedem Mittelerde-Trip bereit.'),
    'Geschichte':     ('⚔️', 'Mittelaltermarkt',                 'Die Vergangenheit war dein Multiplex.'),
    'Horror':         ('👻', 'Teilen wir uns auf!',              'Freddys Liebling. Dunkelheit ist dein Freund.'),
    'Komödie':        ('😂', 'Nur ne Fleischwunde',              'Du lachst, wo andere gähnen.'),
    'Kriegsfilm':     ('🪖', 'War never changes',                'Du schaust Kriegsfilme — und findest sie gut.'),
    'Krimi':          ('🔫', 'Good Cop or Bad Cop?',             'Ein Angebot, das du nicht ablehnen kannst.'),
    'Liebesfilm':     ('💕', 'In the Mood for Love',             'Du glaubst an große Gefühle auf der Leinwand.'),
    'Musikfilm':      ('🎸', 'Dancing Queen',                    'Wenn Musik zur Hauptrolle wird, bist du dabei.'),
    'Musical':        ('🎭', 'Welcome to La La Land',            'Du singst innerlich mit. Sehr laut.'),
    'Mystery':        ('🔍', 'The unreliable Narrator',          '"Nur noch eine Frage..." Du liebst Rätsel.'),
    'Science-Fiction':('🚀', 'May the fourth be with you',       'Logisch. Und: Raumschiffe sind cool.'),
    'Sportfilm':      ('🏆', 'Underdog',                         'Underdog-Storys treffen dich jedes Mal neu.'),
    'Thriller':       ('😬', "What's in the Box?",               'Spannung ist dein zweiter Vorname.'),
    'Western':        ('🌵', 'High Noon',                        'The Good, the Bad, and — du.'),
}

# Negative Genre-Achievements (höherer Threshold als positiv — ca. doppelt)
GENRE_HATE_ACHIEVEMENTS = {
    'Action':         ('🕊️', 'Feuerpause',                          'Explosionen und Verfolgungsjagden? Du brauchst das nicht.'),
    'Abenteuer':      ('🛋️', 'Zuhause ist\'s am schönsten',          'Abenteuer? Nur auf dem Sofa.'),
    'Animation':      ('🙅', 'Nur für Kinder',                       'Animiert heißt für dich: nicht für mich.'),
    'Biografie':      ('🤷', 'Wer war das nochmal?',                 'Lebensgeschichten lassen dich kalt.'),
    'Dokumentarfilm': ('📺', 'Kein Arte-Abend',                      'Lehrreich ist kein Kaufargument.'),
    'Drama':          ('🙂', 'Ohne Tränen bitte',                    'Auf emotionale Schwergewichte kannst du verzichten.'),
    'Familienfilm':   ('🚪', 'Kein Familienabend',                   'Herzerwärmend ist nicht dein Modus.'),
    'Fantasy':        ('🐉', 'Keine Drachen',                        'Magie, Elfen, Drachen — du lehnst das ganze Universum ab.'),
    'Geschichte':     ('📅', 'Kein Geschichtsunterricht',            'Die Vergangenheit darf vergangen bleiben.'),
    'Horror':         ('💡', 'Lights on',                            'Lieber wieder Mickey Maus.'),
    'Komödie':        ('🪑', 'Zum Lachen gehst du wohl in den Keller', 'Humor auf der Leinwand zündet bei dir nicht.'),
    'Kriegsfilm':     ('☮️', 'Pazifist',                             'Kriegsfilme sind nicht dein Schlachtfeld.'),
    'Krimi':          ('📁', 'Fall geschlossen',                     'Krimis lösen bei dir nichts aus.'),
    'Liebesfilm':     ('😬', 'Schauder-Schnulzen',                   'Große Gefühle auf der Leinwand? Eher Schauder.'),
    'Musikfilm':      ('🔇', 'Nachtruhe!',                           'Wenn Musik zur Hauptrolle wird, schaltest du ab.'),
    'Musical':        ('🙉', 'Bitte nicht singen!',                  'Wenn alle anfangen zu singen, hörst du auf zu schauen.'),
    'Mystery':        ('🔓', 'Spoiler bitte',                        'Rätsel reizen dich nicht.'),
    'Science-Fiction':('🚫', 'Warp-Verweigerer',                     'Raumschiffe und Zeitreisen? Nicht dein Universum.'),
    'Sportfilm':      ('🏳️', 'Sport ist Mord',                       'Der Außenseiter-Triumph lässt dich kalt.'),
    'Thriller':       ('💅', 'Ent-Spannung',                         'Die Fingernägel bleiben dran. Spannung ist nicht dein zweiter Vorname.'),
    'Western':        ('🌵', 'Läster Western',                       'The Bad, the Bad, and — auch der Ugly war nix.'),
}

# ─────────────────────────────────────────────────────────────────
# HILFSFUNKTIONEN
# ─────────────────────────────────────────────────────────────────

def pearsonr(a, b):
    """Korrelationskoeffizient, gibt NaN zurück wenn zu wenig Daten."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 5:
        return np.nan
    am, bm = a - a.mean(), b - b.mean()
    denom = math.sqrt((am**2).sum() * (bm**2).sum())
    return float((am * bm).sum() / denom) if denom > 0 else np.nan

def shannon_entropy(series):
    """Maß für Genre-Vielfalt. Höher = breiter Geschmack."""
    counts = series.value_counts(normalize=True)
    return float(-np.sum(counts * np.log2(counts + 1e-10)))

def explode_genres(df):
    """Eine Zeile pro Film-Genre-Kombination."""
    rows = []
    for _, row in df.iterrows():
        if not isinstance(row.get('genres'), str):
            continue
        for g in [x.strip() for x in row['genres'].split(',')]:
            if g:
                rows.append({**row.to_dict(), 'genre': g})
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def normalize_title(t):
    """Titelnormalisierung für Matching: NFKD-Decomposition → ASCII, dann a-z0-9."""
    import re
    t = unicodedata.normalize('NFKD', str(t))
    t = t.encode('ascii', 'ignore').decode()
    t = re.sub(r'[^a-z0-9 ]', '', t.lower())
    return re.sub(r'\s+', ' ', t).strip()

# ─────────────────────────────────────────────────────────────────
# DATEN LADEN
# ─────────────────────────────────────────────────────────────────

def load_imdb_export(path, cache_path=None):
    """
    Lädt einen IMDB-Ratings-Export (deutsches oder englisches Interface).
    Gibt zwei DataFrames zurück:
      - df_films:  nur Filme (für Hauptanalyse)
      - df_raw:    alle Einträge inkl. Serien (für Insider-Checks + progressive Achievements)
    """
    raw = pd.read_csv(path, encoding='utf-8')

    # Spalten umbenennen (robust gegenüber kleinen Variationen)
    rename = {
        'Your Rating':    'user_rating',
        'IMDb Rating':    'imdb_rating',
        'Num Votes':      'num_votes',
        'Title':          'title',
        'Original Title': 'original_title',
        'Year':           'year',
        'Genres':         'genres',
        'Directors':      'directors',
        'Runtime (mins)': 'runtime',
        'Date Rated':     'date_rated',
        'Const':          'imdb_id',
        'Title Type':     'title_type',
    }
    raw = raw.rename(columns={k: v for k, v in rename.items() if k in raw.columns})

    for col in ['user_rating', 'imdb_rating', 'num_votes', 'year']:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors='coerce')

    # Titel normalisiert für Matching
    # primary: original_title (für Analyse); alt: title (lokalisiert) für Filmbuddy-Cross-Matching
    title_col = 'original_title' if 'original_title' in raw.columns else 'title'
    raw['title_norm'] = raw[title_col].apply(normalize_title)
    # Alternativer Key für Filmbuddy: lokalisierter Titel (z.B. "Parasite" statt "기생충")
    if title_col == 'original_title' and 'title' in raw.columns:
        raw['title_alt_norm'] = raw['title'].apply(normalize_title)
    else:
        raw['title_alt_norm'] = raw['title_norm']

    # Nur Filme für die Hauptanalyse
    film_types = {'Film', 'Fernsehfilm', 'movie', 'tvMovie', 'Movie'}
    series_types = {'Fernsehserie', 'Miniserie', 'tvSeries', 'tvMiniSeries'}

    df_films = raw[raw['title_type'].isin(film_types)].copy() if 'title_type' in raw.columns else raw.copy()
    df_films = df_films[df_films['user_rating'].notna() & df_films['imdb_rating'].notna()].copy()
    df_films = df_films[df_films['user_rating'].between(1, 10) & df_films['imdb_rating'].between(1, 10)].copy()

    n_films  = len(df_films)
    n_series = len(raw[raw['title_type'].isin(series_types)]) if 'title_type' in raw.columns else 0
    print(f"  Geladen: {n_films} Filme  |  {n_series} Serien  |  {len(raw)} Einträge gesamt")
    print(f"  (Filme mit Genres: {df_films['genres'].notna().sum()})")

    # IMDB-Daten in TMDB-Cache schreiben (hilft LB-Usern mit gleichen Filmen)
    if cache_path:
        try:
            from tmdb_enrich import populate_cache_from_imdb
            populate_cache_from_imdb(df_films, cache_path)
        except Exception:
            pass

    return df_films, raw


def load_letterboxd_export(path, api_key=None, cache_path=None, progress_cb=None):
    """
    Lädt einen Letterboxd-Export (CSV) und reichert ihn optional mit TMDB an.

    Letterboxd-Export-Format:
      Date, Name, Year, Letterboxd URI, Rating  (Rating: 0.5-5 Sterne)

    Gibt (df_films, df_raw) zurück — gleiche Struktur wie load_imdb_export,
    damit der Rest des Scripts unverändert funktioniert.

    api_key:    TMDB-API-Key für Anreicherung (optional, aber empfohlen)
    cache_path: Pfad zur Cache-JSON-Datei (default: neben der CSV)
    """
    raw = pd.read_csv(path, encoding='utf-8')
    raw.columns = [c.strip() for c in raw.columns]

    # Spalten umbenennen
    rename = {
        'Name':           'title',
        'Year':           'year',
        'Rating':         'lb_rating',
        'Date':           'date_rated',
        'Letterboxd URI': 'lb_uri',
    }
    raw = raw.rename(columns={k: v for k, v in rename.items() if k in raw.columns})

    raw['lb_rating'] = pd.to_numeric(raw.get('lb_rating', pd.Series(dtype=float)), errors='coerce')
    raw['year']      = pd.to_numeric(raw.get('year',      pd.Series(dtype=float)), errors='coerce')
    raw = raw[raw['lb_rating'].notna() & (raw['lb_rating'] > 0)].copy()

    # Letterboxd 0.5-5 → 1-10 (IMDB-Skala)
    raw['user_rating'] = (raw['lb_rating'] * 2).round(1)

    # Slug aus URI
    if 'lb_uri' in raw.columns:
        raw['title_norm'] = raw['title'].apply(normalize_title)

    # TMDB-Anreicherung
    if api_key:
        if cache_path is None:
            cache_path = os.path.join(os.path.dirname(os.path.abspath(path)), 'tmdb_cache.json')
        try:
            from tmdb_enrich import enrich_letterboxd
            print('  TMDB-Anreicherung läuft...')
            raw = enrich_letterboxd(raw, api_key, cache_path=cache_path, progress_cb=progress_cb)
            if 'tmdb_rating' in raw.columns:
                raw['imdb_rating'] = pd.to_numeric(raw['tmdb_rating'], errors='coerce')
            if 'vote_count' in raw.columns:
                raw['num_votes'] = pd.to_numeric(raw['vote_count'], errors='coerce')
            n_enriched = raw['imdb_rating'].notna().sum() if 'imdb_rating' in raw.columns else 0
            print(f'  TMDB: {n_enriched}/{len(raw)} Filme mit Rating angereichert')
        except ImportError:
            print('  Warnung: tmdb_enrich.py nicht gefunden — keine Genres/Regisseure')
        except Exception as _enrich_err:
            import traceback as _tb
            raw['_enrich_error'] = f'{type(_enrich_err).__name__}: {_enrich_err}'[:200]
            print(f'  FEHLER bei TMDB-Anreicherung: {_enrich_err}\n{_tb.format_exc()[-400:]}')
            # Fallback: leere Spalten damit der Rest der App funktioniert
            if 'imdb_rating' not in raw.columns:
                raw['imdb_rating'] = float('nan')
            if 'genres' not in raw.columns:
                raw['genres'] = float('nan')
            if 'directors' not in raw.columns:
                raw['directors'] = float('nan')
    else:
        raw['imdb_rating'] = float('nan')
        raw['genres']      = float('nan')
        raw['directors']   = float('nan')

    raw['title_norm'] = raw['title'].apply(normalize_title)
    raw['title_type'] = 'movie'   # LB-Export enthält nur Filme

    df_films = raw[raw['user_rating'].notna()].copy()

    n_films = len(df_films)
    has_genres = df_films['genres'].notna().sum() if 'genres' in df_films.columns else 0
    print(f'  Geladen (Letterboxd): {n_films} Filme')
    print(f'  (mit TMDB-Genres: {has_genres}, mit IMDB-Rating: {df_films["imdb_rating"].notna().sum()})')

    return df_films, raw


def detect_and_load(path, api_key=None, cache_path=None, progress_cb=None):
    """
    Erkennt automatisch ob es ein IMDB- oder Letterboxd-Export ist
    und lädt entsprechend.
    """
    try:
        header = pd.read_csv(path, nrows=0, encoding='utf-8')
        cols = set(header.columns)
    except Exception as e:
        raise ValueError(f'CSV konnte nicht gelesen werden: {e}')

    imdb_cols = {'Your Rating', 'IMDb Rating', 'Const'}
    lb_cols   = {'Name', 'Letterboxd URI'}

    if imdb_cols & cols:
        print('  Format erkannt: IMDB-Export')
        return load_imdb_export(path, cache_path=cache_path)
    elif lb_cols & cols or 'Name' in cols:
        print('  Format erkannt: Letterboxd-Export')
        return load_letterboxd_export(path, api_key=api_key, cache_path=cache_path, progress_cb=progress_cb)
    else:
        raise ValueError(
            f'Unbekanntes CSV-Format. Spalten: {list(cols)}\n'
            'Erwartet: IMDB-Export oder Letterboxd-Export.'
        )


def load_david_robert(script_dir):
    """
    Lädt David- und Robert-Ratings.

    Priorität:
      1. david_ratings.csv / robert_ratings.csv  (vollständiger Scrape via scrape_letterboxd.py)
      2. blockbusters_1960_2025.csv              (Fallback, nur 660 Blockbuster)

    Gibt (david_df, robert_df) zurück, jeweils mit Spalten:
      title_norm, year, david_rating / robert_rating  (auf 1–10 Skala)
    """

    def _from_full_csv(path, out_col):
        df = pd.read_csv(path, encoding='utf-8')
        df['year'] = pd.to_numeric(df['year'] if 'year' in df.columns else pd.Series(dtype=float), errors='coerce')
        df['rating'] = pd.to_numeric(df['rating'], errors='coerce')
        df = df[df['rating'].notna()].copy()
        df[out_col] = df['rating'] * 2            # Letterboxd 0.5–5 → 1–10
        title_col = 'title' if 'title' in df.columns else df.columns[1]
        df['title_norm'] = df[title_col].apply(normalize_title)
        return df[['title_norm', 'year', out_col]]

    david_path  = os.path.join(script_dir, 'david_ratings.csv')
    robert_path = os.path.join(script_dir, 'robert_ratings.csv')
    bb_path     = os.path.join(script_dir, 'blockbusters_1960_2025.csv')

    david_df = robert_df = None

    # Vollständige Scrape-Daten (bevorzugt)
    if os.path.exists(david_path):
        david_df = _from_full_csv(david_path, 'david_rating')
        print(f"  David : {len(david_df):>5} Ratings  ← david_ratings.csv (vollständig)")
    if os.path.exists(robert_path):
        robert_df = _from_full_csv(robert_path, 'robert_rating')
        print(f"  Robert: {len(robert_df):>5} Ratings  ← robert_ratings.csv (vollständig)")

    # Fallback: Blockbuster-CSV
    if (david_df is None or robert_df is None) and os.path.exists(bb_path):
        bb = pd.read_csv(bb_path)
        for col in ['year', 'davids_score_letterboxd', 'roberts_score_letterboxd']:
            bb[col] = pd.to_numeric(bb[col], errors='coerce')
        bb['title_norm'] = bb['title'].apply(normalize_title)

        if david_df is None:
            d = bb[bb['davids_score_letterboxd'].notna()].copy()
            d['david_rating'] = d['davids_score_letterboxd'] * 2
            david_df = d[['title_norm', 'year', 'david_rating']]
            print(f"  David : {len(david_df):>5} Ratings  ← blockbusters_1960_2025.csv (nur Blockbuster!)")
            print("           → Tipp: scrape_letterboxd.py ausführen für vollständige Daten")

        if robert_df is None:
            r = bb[bb['roberts_score_letterboxd'].notna()].copy()
            r['robert_rating'] = r['roberts_score_letterboxd'] * 2
            robert_df = r[['title_norm', 'year', 'robert_rating']]
            print(f"  Robert: {len(robert_df):>5} Ratings  ← blockbusters_1960_2025.csv (nur Blockbuster!)")

    if david_df is None and robert_df is None:
        print("  Keine David/Robert-Daten gefunden.")
        print("  → scrape_letterboxd.py ausführen und die CSVs ins gleiche Verzeichnis legen.")

    return david_df, robert_df


# ─────────────────────────────────────────────────────────────────
# 1. PERSÖNLICHKEITSDIMENSIONEN
# ─────────────────────────────────────────────────────────────────

def compute_dimensions(df, rating_source='IMDB'):
    """
    Berechnet 4 Persönlichkeitsdimensionen (Myers-Briggs-Stil).
    Jede hat zwei Extreme und eine Mitte.
    Gibt Dict zurück: {dimension: {pole, score, desc}}

    rating_source: 'IMDB' oder 'TMDB' — steuert Label in Beschreibungen
    """
    dims = {}
    RS = rating_source  # kurz für f-strings

    # ── D1: Streng ↔ Mild ────────────────────────────────────────
    # Ø(eigene Bewertung - crowd-Schnitt) — negativ = strenger als Massen
    diff = (df['user_rating'] - df['imdb_rating']).mean()
    if diff < DIM_STRENG_THRESHOLD:
        pole = 'Streng'
        desc = f'Du bewertest im Schnitt {abs(diff):.1f} Punkte unter {RS}. Anspruchsvoll.'
    elif diff > DIM_MILD_THRESHOLD:
        pole = 'Mild'
        desc = f'Du bewertest im Schnitt {diff:.1f} Punkte über {RS}. Großzügig.'
    else:
        pole = 'Ausgewogen'
        desc = f'Deine Ratings liegen nah am {RS}-Schnitt (Δ={diff:+.2f}).'
    dims['bewertungsstil'] = {'pole': pole, 'score': round(diff, 3), 'desc': desc,
                               'label': 'Bewertungsstil', 'emoji': '🎯'}

    # ── D2: Diplomat ↔ Polarisierer ──────────────────────────────
    # σ(user−crowd): misst Inkonsistenz der eigenen Abweichungen vom Konsens —
    # nicht ob man generell streng/mild ist (das ist D1), sondern ob man bei
    # manchen Filmen stark abweicht und bei anderen gar nicht.
    # Fallback auf σ(own ratings) wenn keine Crowd-Daten.
    # MSE(user−crowd): große Abweichungen werden quadratisch stärker gewichtet.
    has_imdb_d2 = 'imdb_rating' in df.columns and df['imdb_rating'].notna().sum() >= 10
    if has_imdb_d2:
        _diff_d2 = (df['user_rating'] - df['imdb_rating']).dropna()
        mse = float((_diff_d2 ** 2).mean())
        _mse_label = f'MSE(user−{RS})'
    else:
        _mean_r = df['user_rating'].mean()
        mse = float(((df['user_rating'] - _mean_r) ** 2).mean())
        _mse_label = 'MSE'
    if mse > DIM_POLAR_THRESHOLD:
        pole = 'Polarisierer'
        desc = (f'Deine Abweichungen von {RS} sind sehr inkonsistent — bei manchen Filmen '
                f'liebst du, was andere hassen, und umgekehrt ({_mse_label}={mse:.2f}).')
    elif mse < DIM_DIPLO_THRESHOLD:
        pole = 'Diplomat'
        desc = (f'Du bewertest Filme sehr ähnlich wie der {RS}-Konsens — '
                f'kein starkes Außenseiterprofil ({_mse_label}={mse:.2f}).')
    else:
        pole = 'Ausgewogen'
        desc = f'Weder durchgehend Abweichler noch {RS}-Klon ({_mse_label}={mse:.2f}).'
    dims['meinungsstaerke'] = {'pole': pole, 'score': round(mse, 3), 'desc': desc,
                                'label': 'Meinungsstärke', 'emoji': '💥'}

    # ── D3: Spezialist ↔ Omnivore ────────────────────────────────
    # Shannon-Entropie der Genre-Verteilung
    if 'genres' in df.columns and df['genres'].notna().sum() >= 10:
        gdf = explode_genres(df)
        if not gdf.empty:
            entropy = shannon_entropy(gdf['genre'])
            n_genres = gdf['genre'].nunique()
            max_entropy = math.log2(n_genres) if n_genres > 1 else 1
            norm_e = entropy / max_entropy
            if norm_e < DIM_SPEZIALIST_ENTROPY:
                pole = 'Spezialist'
                desc = f'Du hast klare Genre-Lieblinge (Vielfalt: {norm_e:.0%}, {n_genres} Genres).'
            elif norm_e > DIM_OMNIVORE_ENTROPY:
                pole = 'Omnivore'
                desc = f'Du schaust wirklich alles quer (Vielfalt: {norm_e:.0%}, {n_genres} Genres).'
            else:
                pole = 'Ausgewogen'
                desc = f'Guter Mix aus Vorlieben und Offenheit (Vielfalt: {norm_e:.0%}).'
            dims['geschmacksbreite'] = {'pole': pole, 'score': round(norm_e, 3), 'desc': desc,
                                         'label': 'Geschmacksbreite', 'emoji': '🌍'}

    # ── D4: Klassiker ↔ Zeitgeist ────────────────────────────────
    # Median-Jahr der gesehenen Filme — misst WANN man schaut, nicht wie man bewertet
    if 'year' in df.columns and df['year'].notna().sum() >= 10:
        med_year = int(df['year'].dropna().median())
        if med_year < DIM_KLASSIKER_YEAR:
            pole = 'Klassiker'
            desc = f'Dein Medianjahr: {med_year} — du liebst das Kino von früher.'
        elif med_year > DIM_ZEITGEIST_YEAR:
            pole = 'Zeitgeist'
            desc = f'Dein Medianjahr: {med_year} — du schaust hauptsächlich Neues.'
        else:
            pole = 'Ausgewogen'
            desc = f'Du schaust quer durch die Jahrzehnte (Medianjahr: {med_year}).'
        dims['epoche'] = {'pole': pole, 'score': med_year, 'desc': desc,
                           'label': 'Lieblingsepoche', 'emoji': '🕰️'}

    # ── D5: Blockbuster ↔ Arthouse ────────────────────────────────────
    _nv_col = 'num_votes' in df.columns
    _nv_cnt = df['num_votes'].notna().sum() if _nv_col else 0
    print(f'  D5: num_votes col={_nv_col}, notna={_nv_cnt}, rating_source={RS}')
    if _nv_col and _nv_cnt >= 20:
        _is_tmdb    = (RS == 'TMDB')
        _bb_thresh  = BLOCKBUSTER_VOTES_TMDB     if _is_tmdb else BLOCKBUSTER_VOTES
        _art_thresh = ARTHOUSE_VOTES_TMDB        if _is_tmdb else ARTHOUSE_VOTES
        _bb_min     = BLOCKBUSTER_MIN_FILMS_TMDB  if _is_tmdb else BLOCKBUSTER_MIN_FILMS
        _art_min    = ARTHOUSE_MIN_FILMS_TMDB     if _is_tmdb else ARTHOUSE_MIN_FILMS

        overall_bias_d5 = float((df['user_rating'] - df['imdb_rating']).mean())
        bb  = df[(df['num_votes'] >= _bb_thresh)  & df['imdb_rating'].notna()]
        art = df[(df['num_votes'] <= _art_thresh) & df['imdb_rating'].notna()]
        _nv_vals = df['num_votes'].dropna()
        print(f'  D5: bb_thresh={_bb_thresh}, art_thresh={_art_thresh}, '
              f'n_bb={len(bb)}, n_art={len(art)}, '
              f'votes median={_nv_vals.median():.0f}, min={_nv_vals.min():.0f}, max={_nv_vals.max():.0f}')
        if len(bb) >= _bb_min and len(art) >= _art_min:
            bb_adj  = float((bb['user_rating']  - bb['imdb_rating']).mean())  - overall_bias_d5
            art_adj = float((art['user_rating'] - art['imdb_rating']).mean()) - overall_bias_d5
            score   = round(bb_adj - art_adj, 3)
            _src    = 'TMDB' if _is_tmdb else 'IMDB'
            if score > 0.3:
                pole = 'Blockbuster-Fan'
                desc = (f'Du bewertest große Kassenschlager (>{_bb_thresh//1000}k {_src}-Votes) '
                        f'relativ {score:.2f} Punkte besser als Arthouse-Filme '
                        f'(n_bb={len(bb)}, n_art={len(art)}).')
            elif score < -0.3:
                pole = 'Arthouse-Aficionado'
                desc = (f'Du bewertest Nischenfilme (<{_art_thresh//1000}k {_src}-Votes) '
                        f'relativ {abs(score):.2f} Punkte besser als Blockbuster '
                        f'(n_bb={len(bb)}, n_art={len(art)}).')
            else:
                pole = 'Ausgewogen'
                desc = (f'Kein systematischer Unterschied zwischen Blockbuster- und '
                        f'Arthouse-Bewertungen (Diff={score:+.2f}, '
                        f'n_bb={len(bb)}, n_art={len(art)}).')
            dims['publikum'] = {'pole': pole, 'score': score, 'desc': desc,
                                'label': 'Publikumsgeschmack', 'emoji': '🎪'}

    return dims


# ─────────────────────────────────────────────────────────────────
# 2. BONUS-ACHIEVEMENTS
# ─────────────────────────────────────────────────────────────────

def compute_bonus_achievements(df, birth_year=None, david_df=None, robert_df=None, rating_source='IMDB'):
    """
    Berechnet Bonus-Achievements.
    Gibt Liste von Dicts zurück: {emoji, name, desc}
    """
    ach = []
    RS = rating_source
    overall_avg = df['user_rating'].mean()

    # ── The real OG ──────────────────────────────────────────────────
    if 'year' in df.columns and df['year'].notna().sum() >= 10:
        _med = int(df['year'].dropna().median())
        if _med < 2000:
            ach.append({'emoji': '📽️', 'name': 'The real OG',
                        'desc': f'Dein Medianjahr ist {_med} — du schaust hauptsächlich Filme aus dem letzten Jahrhundert. Old school ist keine Einstellung, es ist eine Lebensweise.'})

    # ── hasst Filme mehr als David Hain 😉 ──────────────────────────
    if overall_avg <= 5.5:
        ach.append({'emoji': '😬', 'name': 'hasst Filme mehr als David Hain 😉',
                    'desc': f'Dein Schnitt: {overall_avg:.1f}/10. Du bist strenger als unser strengster Kritiker. Wir machen uns ehrlich gesagt Sorgen.'})

    # ── Mainstreamer / Rebell ─────────────────────────────────────
    corr = pearsonr(df['user_rating'].values, df['imdb_rating'].values)
    if not np.isnan(corr):
        if corr > MAINSTREAMER_CORR:
            ach.append({'emoji': '📺', 'name': 'Mainstreamer',
                        'desc': f'Deine Ratings folgen {RS} fast exakt (r={corr:.2f}). Du bist der Algorithmus — und der streamt auch.'})
        elif corr < REBELL_CORR:
            ach.append({'emoji': '🏴‍☠️', 'name': 'Rebell',
                        'desc': f'Negative Korrelation mit {RS} (r={corr:.2f}). Du liebst, was alle hassen — oder hasst, was alle lieben.'})

    # ── Blockbuster-Bro / Arthouse-Snob ──────────────────────────
    if 'num_votes' in df.columns:
        _is_tmdb_b = (RS == 'TMDB')
        _bb_v   = BLOCKBUSTER_VOTES_TMDB     if _is_tmdb_b else BLOCKBUSTER_VOTES
        _art_v  = ARTHOUSE_VOTES_TMDB        if _is_tmdb_b else ARTHOUSE_VOTES
        _bb_min = BLOCKBUSTER_MIN_FILMS_TMDB  if _is_tmdb_b else BLOCKBUSTER_MIN_FILMS
        bb_films = df[df['num_votes'] >= _bb_v]
        art_films = df[df['num_votes'] <= _art_v]
        if len(bb_films) >= _bb_min:
            bb_diff = bb_films['user_rating'].mean() - overall_avg
            if bb_diff > BLOCKBUSTER_DIFF:
                ach.append({'emoji': '🍿', 'name': 'Blockbuster-Bro',
                            'desc': f'Kassenschlager bewertest du um {bb_diff:.1f} Punkte besser als deinen Gesamtschnitt (n={len(bb_films)}).'})
        if len(art_films) >= ARTHOUSE_MIN_FILMS:
            art_diff = art_films['user_rating'].mean() - overall_avg
            if art_diff > ARTHOUSE_DIFF:
                ach.append({'emoji': '🎭', 'name': 'Arthouse-Snob',
                            'desc': f'Unbekannte Indie-Filme liebst du um {art_diff:.1f} Punkte mehr als deinen Gesamtschnitt (n={len(art_films)}).'})

    # ── Hidden Gem Hunter ─────────────────────────────────────────
    gems = df[(df['imdb_rating'] < HIDDEN_GEM_IMDB_MAX) & (df['user_rating'] >= HIDDEN_GEM_USER_MIN)]
    if len(gems) >= HIDDEN_GEM_MIN:
        ach.append({'emoji': '💎', 'name': 'Hidden Gem Hunter',
                    'desc': f'{len(gems)} Geheimtipps entdeckt: {RS} sagt Ø{gems["imdb_rating"].mean():.1f}, du sagst Ø{gems["user_rating"].mean():.1f}.'})

    # ── Trash-King ────────────────────────────────────────────────
    # Mind. 5 Filme wo IMDB < 5.0 aber du gibst ≥ 8 — du liebst Trash
    trash = df[(df['imdb_rating'] < TRASH_KING_IMDB_MAX) & (df['user_rating'] >= TRASH_KING_USER_MIN)]
    if len(trash) >= TRASH_KING_MIN:
        ach.append({'emoji': '🗑️', 'name': 'Trash-King',
                    'desc': f'Du hast {len(trash)} objektiven Trash-Filme mit ≥8 bewertet. Kein Urteilsvermögen oder brillante Ironie?'})

    # ── Nostalgiker / Anti-Nostalgiker ────────────────────────────
    if birth_year and 'year' in df.columns:
        form_start, form_end = birth_year, birth_year + 20
        form    = df[df['year'].between(form_start, form_end - 1)]
        nonform = df[~df['year'].between(form_start, form_end - 1)]
        if len(form) >= NOSTALGIE_MIN_FILMS and len(nonform) >= NOSTALGIE_MIN_FILMS:
            diff = form['user_rating'].mean() - nonform['user_rating'].mean()
            if diff > NOSTALGIE_DIFF:
                ach.append({'emoji': '💝', 'name': 'Nostalgiker',
                            'desc': f'Filme aus deiner Jugend ({form_start}–{form_end-1}) bewertest du um {diff:.1f} Punkte höher (n={len(form)}).'})
            elif diff < ANTI_NOSTALGIE_DIFF:
                ach.append({'emoji': '😈', 'name': 'Antichrist',
                            'desc': f'Deine Jugendfilme ({form_start}–{form_end-1}) bewertest du um {abs(diff):.1f} Punkte schlechter als den Rest. Du hast deine Kindheit wohl nicht so geliebt. (n={len(form)})'})

    # ── Team David / Team Robert ──────────────────────────────────
    corr_d, corr_r = np.nan, np.nan
    n_d, n_r = 0, 0

    if david_df is not None:
        merged = df.merge(david_df[['title_norm', 'year', 'david_rating']],
                          on=['title_norm', 'year'], how='inner')
        n_d = len(merged)
        if n_d >= 10:
            corr_d = pearsonr(merged['user_rating'].values, merged['david_rating'].values)

    if robert_df is not None:
        merged_r = df.merge(robert_df[['title_norm', 'year', 'robert_rating']],
                            on=['title_norm', 'year'], how='inner')
        n_r = len(merged_r)
        if n_r >= 10:
            corr_r = pearsonr(merged_r['user_rating'].values, merged_r['robert_rating'].values)

    if not np.isnan(corr_d) or not np.isnan(corr_r):
        if not np.isnan(corr_d) and not np.isnan(corr_r):
            if corr_d > corr_r:
                ach.append({'emoji': '🔴', 'name': 'Team David',
                            'desc': f'Dein Geschmack liegt näher bei David (r={corr_d:.2f}, n={n_d}) als bei Robert (r={corr_r:.2f}, n={n_r}).'})
            else:
                ach.append({'emoji': '🔵', 'name': 'Team Robert',
                            'desc': f'Dein Geschmack liegt näher bei Robert (r={corr_r:.2f}, n={n_r}) als bei David (r={corr_d:.2f}, n={n_d}).'})
        elif not np.isnan(corr_d):
            ach.append({'emoji': '🔴', 'name': 'Team David',
                        'desc': f'Korrelation mit David: r={corr_d:.2f} (n={n_d}). Robert hat zu wenig Überschneidungen.'})
        elif not np.isnan(corr_r):
            ach.append({'emoji': '🔵', 'name': 'Team Robert',
                        'desc': f'Korrelation mit Robert: r={corr_r:.2f} (n={n_r}). David hat zu wenig Überschneidungen.'})

    # ── Würde sogar gegen David im Armdrücken verlieren 💪 ──────────
    # Trigger: User hasst Sportfilme mehr als David
    # Benötigt: Genres (aus TMDB/IMDB) + David-Ratings für Schnittmenge
    if david_df is not None and 'genres' in df.columns:
        try:
            _sport_user = df[df['genres'].str.contains('Sportfilm', na=False)]
            if len(_sport_user) >= 3:
                _user_sport_avg = _sport_user['user_rating'].mean()
                # David-Schnitt für dieselben Sportfilme ermitteln
                _sport_merged = _sport_user.merge(
                    david_df[['title_norm', 'year', 'david_rating']],
                    on=['title_norm', 'year'], how='inner'
                )
                if len(_sport_merged) >= 3:
                    _david_sport_avg = _sport_merged['david_rating'].mean()
                    if _user_sport_avg < _david_sport_avg:
                        ach.append({
                            'emoji': '🤼',
                            'name': 'Würde sogar gegen David im Armdrücken verlieren',
                            'desc': (
                                f'David hasst Sportfilme — aber du noch mehr. '
                                f'Dein Schnitt: {_user_sport_avg:.1f}, Davids Schnitt: {_david_sport_avg:.1f} '
                                f'(auf {len(_sport_merged)} gemeinsamen Sportfilmen). '
                                f'Selbst beim Armdrücken bist du der Underdog.'
                            )
                        })
        except Exception:
            pass

    return ach


# ─────────────────────────────────────────────────────────────────
# 3. GENRE-ACHIEVEMENTS
# ─────────────────────────────────────────────────────────────────

def compute_genre_achievements(df):
    """
    Prüft für jedes Genre positive und negative Achievements.
    Positiv: user_avg_genre - user_gesamt_avg >= GENRE_DIFF_THRESHOLD
    Negativ (hasst): adj <= min(-GENRE_DIFF_THRESHOLD, -1.6/sqrt(n/5))  [adaptiv, Floor = pos. Threshold]
    """
    if 'genres' not in df.columns or df['genres'].notna().sum() < 10:
        return []

    overall_avg = df['user_rating'].mean()
    has_imdb = df['imdb_rating'].notna().sum() > 0
    overall_bias = float((df['user_rating'] - df['imdb_rating']).mean()) if has_imdb else 0.0

    gdf = explode_genres(df)
    if gdf.empty:
        return []

    ach = []
    for genre, (emoji, name, desc) in GENRE_ACHIEVEMENTS.items():
        sub = gdf[gdf['genre'] == genre]
        if len(sub) < GENRE_MIN_FILMS:
            continue

        # Adaptiver Threshold: Je weniger Filme, desto stärker muss der Effekt sein.
        # Formel: max(Floor, 0.8 / sqrt(n/5))
        # n=5→0.80, n=20→0.40, n=80→0.20, n=320→0.10 (Floor)
        # Floor 0.1: bei ~320+ Filmen in einem Genre reicht eine kleine, aber konsistente Präferenz.
        n_pos = len(sub)
        adaptive_pos_threshold = max(0.1, 0.8 / math.sqrt(n_pos / 5))

        diff = sub['user_rating'].mean() - overall_avg
        if diff >= adaptive_pos_threshold:
            ach.append({
                'emoji': emoji, 'name': name,
                'desc': f'{desc}  [{genre}: Ø{sub["user_rating"].mean():.1f}, +{diff:.1f} über deinem Schnitt, n={n_pos}]'
            })

        # Hate achievement — ca. doppelter Threshold vs. positiv (Floor -0.4 statt -0.2)
        if has_imdb and genre in GENRE_HATE_ACHIEVEMENTS:
            sub_rated = sub[sub['imdb_rating'].notna()]
            n_neg = len(sub_rated)
            if n_neg >= GENRE_MIN_FILMS:
                # Adaptiver Threshold: bei kleinen n strenger (Noise-Schutz),
                # konvergiert bei großen n zum symmetrischen -GENRE_DIFF_THRESHOLD
                # n=5→-1.6, n=20→-0.8, n=320→-0.2 (Floor = pos. Threshold)
                adaptive_hate_threshold = min(-GENRE_DIFF_THRESHOLD, -1.6 / math.sqrt(n_neg / 5))
                adj = (sub_rated['user_rating'].mean() - sub_rated['imdb_rating'].mean()) - overall_bias
                if adj <= adaptive_hate_threshold:
                    h_emoji, h_name, h_desc = GENRE_HATE_ACHIEVEMENTS[genre]
                    ach.append({
                        'emoji': h_emoji, 'name': h_name,
                        'desc': f'{h_desc}  [{genre}: adj={adj:+.2f}, n={n_neg}]'
                    })
    return ach


# ─────────────────────────────────────────────────────────────────
# 4. INSIDER-ACHIEVEMENTS (Podcast-Injokes)
# ─────────────────────────────────────────────────────────────────

def compute_insider_achievements(df_films, df_raw):
    """
    Podcast-spezifische Achievements.
    df_films = nur Filme (für Geschmacks-Checks)
    df_raw   = gesamter Export inkl. Serien (für Show-Checks)
    """
    ach = []

    # ── Tony Surroundi ────────────────────────────────────────────
    # Trigger: User hat "Zwei wie Pech und Schwafel" auf IMDB bewertet.
    # Nur echte Community-Mitglieder können dieses Achievement freischalten.
    if 'title_norm' in df_raw.columns:
        found = df_raw[df_raw['title_norm'].apply(
            lambda t: any(show in t for show in TONY_SHOW_TITLES)
        )]
        if len(found) > 0:
            rating_info = ''
            if 'user_rating' in found.columns and found['user_rating'].notna().any():
                r = found['user_rating'].dropna().iloc[0]
                rating_info = f' (deine Bewertung: {int(r)}/10)'
            ach.append({
                'emoji': '🔊', 'name': 'Tony Surroundi',
                'desc': (f'Du hast "Zwei wie Pech und Schwafel" auf IMDB bewertet{rating_info}. '
                         f'Willkommen in der Familie. Surround Sound läuft.')
            })

    # ── Weitere Insider-Achievements hier ergänzen ─────────────────
    # Schema:
    # if [bedingung auf df_raw oder df_films]:
    #     ach.append({'emoji': '🎙️', 'name': 'Achievement-Name', 'desc': 'Beschreibung'})

    return ach


# ─────────────────────────────────────────────────────────────────
# 5b. PROGRESSIVE ACHIEVEMENTS (Milestones)
# ─────────────────────────────────────────────────────────────────

def compute_progressive_achievements(df_raw):
    """
    Milestones für Anzahl bewerteter Filme und Serien.
    Gibt jeweils das höchste erreichte Milestone zurück (nicht alle).
    Serien = Fernsehserie + Miniserie, NICHT einzelne Episoden.
    """
    ach = []

    if 'title_type' not in df_raw.columns:
        return ach

    film_types   = {'Film', 'Fernsehfilm', 'movie', 'tvMovie', 'Movie'}
    series_types = {'Fernsehserie', 'Miniserie', 'tvSeries', 'tvMiniSeries'}

    n_films  = df_raw[df_raw['title_type'].isin(film_types)]['user_rating'].notna().sum()
    n_series = df_raw[df_raw['title_type'].isin(series_types)]['user_rating'].notna().sum()

    # Höchstes erreichtes Film-Milestone
    earned_film = None
    for threshold, emoji, name, desc in FILM_MILESTONES:
        if n_films >= threshold:
            earned_film = {'emoji': emoji, 'name': name,
                           'desc': f'{desc}  ({n_films} Filme bewertet)'}
    if earned_film:
        ach.append(earned_film)

    # Höchstes erreichtes Serien-Milestone
    earned_series = None
    for threshold, emoji, name, desc in SERIES_MILESTONES:
        if n_series >= threshold:
            earned_series = {'emoji': emoji, 'name': name,
                             'desc': f'{desc}  ({n_series} Serien bewertet)'}
    if earned_series:
        ach.append(earned_series)

    return ach


# ─────────────────────────────────────────────────────────────────
# 5. TOP / FLOP ANALYSE
# ─────────────────────────────────────────────────────────────────

def compute_top_flop(df, top_n=3, min_films_genre=3, min_films_dir=3):
    """
    Genre- und Regisseur-Statistiken.
    adj = (user_avg - imdb_avg) - overall_user_bias
       → positiv: User mag dieses Genre mehr als seinen eigenen Schnitt vermuten lässt
       → negativ: weniger
    """
    results = {}

    # Gesamtbias des Users (Ø eigene - Ø IMDB über alle Filme)
    overall_bias = float((df['user_rating'] - df['imdb_rating']).mean())
    results['overall_bias'] = overall_bias

    # ── Genres ───────────────────────────────────────────────────
    if 'genres' in df.columns and df['genres'].notna().sum() >= 10:
        gdf = explode_genres(df)
        if not gdf.empty:
            stats = gdf.groupby('genre').agg(
                n        = ('user_rating', 'count'),
                user_avg = ('user_rating', 'mean'),
                imdb_avg = ('imdb_rating', 'mean'),
            ).round(3)
            stats['vs_imdb'] = (stats['user_avg'] - stats['imdb_avg']).round(3)
            stats['adj']     = (stats['vs_imdb'] - overall_bias).round(3)
            stats = stats[stats['n'] >= min_films_genre]
            # Alle Genres sortiert nach adj
            results['genre_all']  = stats.sort_values('adj', ascending=False)
            results['genre_top']  = stats.sort_values('adj', ascending=False).head(top_n)
            results['genre_flop'] = stats.sort_values('adj').head(top_n)

    # ── Regisseure ────────────────────────────────────────────────
    if 'directors' in df.columns and df['directors'].notna().sum() >= 5:
        dir_rows = []
        for _, row in df.iterrows():
            if not isinstance(row.get('directors'), str):
                continue
            for d in [x.strip() for x in row['directors'].split(',')]:
                if d:
                    dir_rows.append({'director': d,
                                     'user_rating': row['user_rating'],
                                     'imdb_rating': row['imdb_rating']})
        if dir_rows:
            ddf = pd.DataFrame(dir_rows)
            dstats = ddf.groupby('director').agg(
                n        = ('user_rating', 'count'),
                user_avg = ('user_rating', 'mean'),
                imdb_avg = ('imdb_rating', 'mean'),
            ).round(2)
            dstats['vs_imdb'] = (dstats['user_avg'] - dstats['imdb_avg']).round(2)
            dstats['adj']     = (dstats['vs_imdb'] - overall_bias).round(2)
            dstats = dstats[dstats['n'] >= min_films_dir].sort_values('user_avg', ascending=False)
            results['dir_top']  = dstats.head(top_n)
            results['dir_flop'] = dstats.tail(top_n).sort_values('user_avg')
            results['dir_all']  = dstats

    return results




# ─────────────────────────────────────────────────────────────────
# 6. RADAR CHART
# ─────────────────────────────────────────────────────────────────




def compute_formative_years_stats(df, birth_year):
    """
    Berechnet Formative-Jahre-Bias inkl. Welch-t-Test und p-Wert.
    Gibt None zurück wenn zu wenig Daten.
    """
    if birth_year is None or 'year' not in df.columns:
        return None

    form_start = birth_year
    form_end   = birth_year + 20

    # IMDB-relativ: bereinigt um allgemeine Ären-Unterschiede in IMDB-Bewertungen
    has_imdb = 'imdb_rating' in df.columns and df['imdb_rating'].notna().sum() > len(df) * 0.3
    if has_imdb:
        vals = (df['user_rating'] - df['imdb_rating'])
        form    = vals[df['year'].between(form_start, form_end - 1)].dropna()
        nonform = vals[~df['year'].between(form_start, form_end - 1)].dropna()
        method  = 'IMDB-relativ'
    else:
        form    = df[df['year'].between(form_start, form_end - 1)]['user_rating'].dropna()
        nonform = df[~df['year'].between(form_start, form_end - 1)]['user_rating'].dropna()
        method  = 'Rohrating'

    if len(form) < 5 or len(nonform) < 5:
        return None

    n1, n2 = len(form), len(nonform)
    m1, m2 = float(form.mean()), float(nonform.mean())
    v1, v2 = float(form.var(ddof=1)), float(nonform.var(ddof=1))
    bias   = m1 - m2

    se = np.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return None
    t_stat = bias / se
    # Welch-Satterthwaite degrees of freedom
    df_w = (v1/n1 + v2/n2)**2 / ((v1/n1)**2/(n1-1) + (v2/n2)**2/(n2-1))

    try:
        from scipy import stats as _stats
        p_value = float(2 * _stats.t.sf(abs(t_stat), df=df_w))
    except ImportError:
        # Normal-Approximation (gut genug für n > 30)
        import math
        p_value = float(2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2)))))

    # Cohen's d (gepoolte Standardabweichung) — Effektgröße unabhängig von n
    pooled_std = np.sqrt((v1 * (n1 - 1) + v2 * (n2 - 1)) / (n1 + n2 - 2))
    cohens_d   = bias / pooled_std if pooled_std > 0 else 0.0
    effect_label = 'groß' if abs(cohens_d) >= 0.8 else 'mittel' if abs(cohens_d) >= 0.5 else 'klein'

    # Rohrating-Bias (immer berechnen, auch wenn IMDB-Methode verwendet wird)
    form_raw    = df[df['year'].between(form_start, form_end - 1)]['user_rating'].dropna()
    nonform_raw = df[~df['year'].between(form_start, form_end - 1)]['user_rating'].dropna()
    bias_raw = float(form_raw.mean() - nonform_raw.mean()) \
               if len(form_raw) >= 3 and len(nonform_raw) >= 3 else None

    return {
        'bias':         bias,
        'bias_raw':     round(bias_raw, 3) if bias_raw is not None else None,
        'n_form':       n1,
        'n_nonform':    n2,
        'form_avg':     m1,
        'nonform_avg':  m2,
        't_stat':       t_stat,
        'p_value':      p_value,
        'significant':  p_value < 0.05,
        'cohens_d':     round(cohens_d, 3),
        'effect_label': effect_label,
        'form_start':   form_start,
        'form_end':     form_end - 1,
        'method':       method,
        'has_imdb':     has_imdb,
    }




def save_formative_years_chart(df, birth_year, out_path):
    """
    Balkendiagramm: Ø(user_rating − imdb_rating) pro Jahr.
    Formative Jahre (birth_year bis +19) rot hervorgehoben.
    Fällt kein IMDB-Rating vor, wird Ø user_rating verwendet.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    if 'year' not in df.columns or df['year'].notna().sum() < 5:
        return

    has_imdb = 'imdb_rating' in df.columns and df['imdb_rating'].notna().sum() > len(df) * 0.3
    if has_imdb:
        df = df.copy()
        df['_val'] = df['user_rating'] - df['imdb_rating']
        ylabel = 'Eigene − IMDB Ø'
    else:
        df = df.copy()
        df['_val'] = df['user_rating']
        ylabel = 'Eigene Bewertung Ø'

    yearly = df.dropna(subset=['year', '_val']).copy()
    yearly['year'] = yearly['year'].astype(int)
    yearly = yearly.groupby('year')['_val'].mean()
    years  = yearly.index.values
    vals   = yearly.values

    form_start, form_end = birth_year, birth_year + 19
    colors = ['#e84545' if form_start <= y <= form_end else '#3a3a6a' for y in years]

    fig, ax = plt.subplots(figsize=(8, 3))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    ax.bar(years, vals, color=colors, alpha=0.85, width=0.85, zorder=3)
    if has_imdb:
        ax.axhline(0, color='white', lw=0.8, alpha=0.4, zorder=2)
    ax.axvline(form_start - 0.5, color='#e84545', lw=1, ls='--', alpha=0.5)
    ax.axvline(form_end   + 0.5, color='#e84545', lw=1, ls='--', alpha=0.5)
    ax.text((form_start + form_end) / 2, ax.get_ylim()[1] * 0.92,
            f'Prägende Jahre', ha='center', color='#e84545', fontsize=8)

    ax.set_ylabel(ylabel, color='#888888', fontsize=8)
    ax.tick_params(colors='#888888', labelsize=8, length=0)
    for spine in ax.spines.values():
        spine.set_edgecolor('#2a2a4a')
    ax.yaxis.grid(True, color='#2a2a4a', linewidth=0.5, alpha=0.6, zorder=0)

    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close(fig)


def save_single_dimension_chart(key, df, dims, out_path, rating_source='IMDB'):
    """
    Einzelner Chart für eine Persönlichkeitsdimension (größer als im 2x2-Grid).
    key: 'bewertungsstil' | 'meinungsstaerke' | 'geschmacksbreite' | 'epoche'
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    COLOR  = '#e84545'
    COLOR_P = '#4caf50'
    COLOR_N = '#f44336'
    BG     = '#16213e'
    GRID   = '#2a2a4a'
    TEXT   = '#eaeaea'
    SUBTLE = '#888888'

    fig, ax = plt.subplots(figsize=(7, 3.2))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor(BG)
    pole = dims.get(key, {}).get('pole', '')

    def _style(title, xlabel='', ylabel=''):
        ax.set_facecolor(BG)
        ax.set_title(title, color=TEXT, fontsize=10, fontweight='bold', pad=6)
        ax.tick_params(colors=SUBTLE, labelsize=8, length=0)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID)
        if xlabel: ax.set_xlabel(xlabel, color=SUBTLE, fontsize=8)
        if ylabel: ax.set_ylabel(ylabel, color=SUBTLE, fontsize=8)
        ax.yaxis.grid(True, color=GRID, linewidth=0.5, alpha=0.6, zorder=0)

    if key == 'bewertungsstil':
        counts = [(df['user_rating'] == i).sum() for i in range(1, 11)]
        ax.bar(range(1, 11), counts, color=COLOR, alpha=0.85, width=0.72, zorder=3)
        mean_r = df['user_rating'].mean()
        ax.axvline(mean_r, color='white', lw=1.5, ls='--', alpha=0.85, zorder=5)
        ymax = max(counts) if counts else 1
        ax.text(mean_r + 0.15, ymax * 0.9, f'Ø {mean_r:.1f}', color='white', fontsize=8.5)
        ax.set_xticks(range(1, 11))
        _style(f'Bewertungsstil — {pole}', 'Rating (1–10)', 'Filme')

    elif key == 'meinungsstaerke':
        _has_imdb_chart = 'imdb_rating' in df.columns and df['imdb_rating'].notna().sum() >= 5
        if _has_imdb_chart:
            diff = (df['user_rating'] - df['imdb_rating']).dropna()
            bins = [x - 0.5 for x in range(-9, 11)]
            ax.hist(diff, bins=bins, color=COLOR, alpha=0.85, edgecolor=BG, zorder=3)
            ax.axvline(0, color='white', lw=1.2, ls='--', alpha=0.55, zorder=4)
            ax.axvline(diff.mean(), color='#ffd700', lw=1.8, alpha=0.9, zorder=5)
            ymax2 = ax.get_ylim()[1]
            ax.text(diff.mean() + 0.2, ymax2 * 0.85, f'Ø {diff.mean():+.2f}', color='#ffd700', fontsize=8.5)
            _mse_val = float((diff ** 2).mean())
            ax.text(0.97, 0.93, f'MSE={_mse_val:.2f}', transform=ax.transAxes,
                    color='#aaaaaa', fontsize=8, ha='right', va='top')
            _style(f'Meinungsstärke — {pole}', f'Eigene − {rating_source}', 'Filme')
        else:
            # Fallback ohne Crowd-Daten: Verteilung eigener Ratings
            counts = [(df['user_rating'] == i).sum() for i in range(1, 11)]
            ax.bar(range(1, 11), counts, color=COLOR, alpha=0.85, width=0.72, zorder=3)
            mean_r = df['user_rating'].mean()
            _mse_own = float(((df['user_rating'] - mean_r) ** 2).mean())
            ax.axvline(mean_r, color='white', lw=1.5, ls='--', alpha=0.85, zorder=5)
            ax.text(0.97, 0.93, f'MSE={_mse_own:.2f}', transform=ax.transAxes,
                    color='#aaaaaa', fontsize=8, ha='right', va='top')
            ax.set_xticks(range(1, 11))
            _style(f'Meinungsstärke — {pole}  (kein {rating_source}-Vergleich)', 'Rating (1–10)', 'Filme')

    elif key == 'geschmacksbreite':
        gdf = explode_genres(df)
        if not gdf.empty:
            overall_avg = df['user_rating'].mean()
            gs = gdf.groupby('genre').agg(n=('user_rating','count'), avg=('user_rating','mean'))
            gs = gs[gs['n'] >= 3].copy()  # mind. 3 Filme pro Genre
            gs['diff'] = gs['avg'] - overall_avg
            gs = gs.sort_values('diff').tail(12)
            colors = [COLOR_P if v >= 0 else COLOR_N for v in gs['diff']]
            ax.barh(range(len(gs)), gs['diff'].values, color=colors, alpha=0.85, zorder=3)
            ax.xaxis.grid(True, color=GRID, linewidth=0.5, alpha=0.6, zorder=0)
            ax.yaxis.grid(False)
            ax.set_yticks(range(len(gs)))
            ax.set_yticklabels(gs.index, fontsize=8, color=TEXT)
            ax.axvline(0, color='white', lw=1, alpha=0.5, zorder=4)
        _style(f'Geschmacksbreite — {pole}', 'Diff. zu eigenem Ø', '')

    elif key == 'epoche':
        if 'year' in df.columns:
            years = df['year'].dropna()
            dc = (years // 10 * 10).astype(int).value_counts().sort_index()
            ax.bar(dc.index, dc.values, width=8.5, color=COLOR, alpha=0.85, zorder=3)
            median_y = int(years.median())
            ax.axvline(median_y, color='white', lw=1.5, ls='--', alpha=0.85, zorder=5)
            ax.text(median_y + 2, dc.values.max() * 0.88,
                    'Median ' + str(median_y), color='white', fontsize=8.5)
            ax.set_xticks(dc.index)
            ax.set_xticklabels([f"{d}s" for d in dc.index], rotation=35, ha='right', fontsize=7.5)
        _style(f'Lieblingsepoche — {pole}', '', 'Filme')

    elif key == 'publikum':
        if 'num_votes' in df.columns and df['num_votes'].notna().sum() >= 10:
            _is_tmdb_c = (rating_source == 'TMDB')
            _bb_v  = BLOCKBUSTER_VOTES_TMDB if _is_tmdb_c else BLOCKBUSTER_VOTES
            _art_v = ARTHOUSE_VOTES_TMDB    if _is_tmdb_c else ARTHOUSE_VOTES
            _src   = rating_source
            overall_bias = float((df['user_rating'] - df['imdb_rating']).mean())
            cats = [
                (f'Arthouse\n(<{_art_v//1000}k {_src})',          df[df['num_votes'] <= _art_v]),
                (f'Mitte\n({_art_v//1000}k–{_bb_v//1000}k)',      df[(df['num_votes'] > _art_v) & (df['num_votes'] < _bb_v)]),
                (f'Blockbuster\n(>{_bb_v//1000}k {_src})',         df[df['num_votes'] >= _bb_v]),
            ]
            labels_c, adjs, ns = [], [], []
            for lbl, sub in cats:
                sub = sub.dropna(subset=['user_rating', 'imdb_rating'])
                if len(sub) >= 3:
                    labels_c.append(lbl)
                    adjs.append((sub['user_rating'] - sub['imdb_rating']).mean() - overall_bias)
                    ns.append(len(sub))
            if labels_c:
                bar_colors = [COLOR_P if v >= 0 else COLOR_N for v in adjs]
                ax.bar(range(len(labels_c)), adjs, color=bar_colors, alpha=0.85, width=0.55, zorder=3)
                ax.axhline(0, color='white', lw=1, alpha=0.5, zorder=4)
                ax.set_xticks(range(len(labels_c)))
                ax.set_xticklabels(labels_c, fontsize=8.5, color=TEXT)
                for i, (adj, n) in enumerate(zip(adjs, ns)):
                    ax.text(i, adj + (0.02 if adj >= 0 else -0.04),
                            f'n={n}', ha='center', color='#aaaaaa', fontsize=7.5)
        _style(f'Publikumsgeschmack — {pole}', '', 'Adj. Rating')

    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close(fig)


def save_dimension_detail_charts(name, df, dims, out_path):
    """
    2×2 Detail-Charts für jede Persönlichkeitsdimension:
      - Bewertungsstil:   Histogramm der eigenen Ratings (1–10)
      - Meinungsstärke:   Histogramm der Differenz (eigene − IMDB)
      - Geschmacksbreite: Diverging-Balken (Genre-Präferenz vs. Gesamtschnitt)
      - Lieblingsepoche:  Balken nach Jahrzehnt
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    COLOR   = '#e84545'
    COLOR_P = '#4caf50'
    COLOR_N = '#f44336'
    BG      = '#16213e'
    GRID    = '#2a2a4a'
    TEXT    = '#eaeaea'
    SUBTLE  = '#888888'

    fig, axes = plt.subplots(2, 2, figsize=(10, 7))
    fig.patch.set_facecolor('#1a1a2e')

    def style_ax(ax, title, xlabel='', ylabel=''):
        ax.set_facecolor(BG)
        ax.set_title(title, color=TEXT, fontsize=9.5, fontweight='bold', pad=7)
        ax.tick_params(colors=SUBTLE, labelsize=8, length=0)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID)
        if xlabel:
            ax.set_xlabel(xlabel, color=SUBTLE, fontsize=8)
        if ylabel:
            ax.set_ylabel(ylabel, color=SUBTLE, fontsize=8)

    # ── 1. Bewertungsstil: Rating-Histogramm ─────────────────────
    ax = axes[0, 0]
    pole = dims.get('bewertungsstil', {}).get('pole', '')
    counts = [(df['user_rating'] == i).sum() for i in range(1, 11)]
    ax.bar(range(1, 11), counts, color=COLOR, alpha=0.85, width=0.72, zorder=3)
    ax.yaxis.grid(True, color=GRID, linewidth=0.5, alpha=0.6, zorder=0)
    mean_r = df['user_rating'].mean()
    ax.axvline(mean_r, color='white', lw=1.5, ls='--', alpha=0.85, zorder=5)
    ymax = max(counts) if counts else 1
    ax.text(mean_r + 0.15, ymax * 0.92, f'Ø {mean_r:.1f}', color='white', fontsize=8)
    ax.set_xticks(range(1, 11))
    style_ax(ax, f'Bewertungsstil  [{pole}]', 'Rating (1–10)', 'Filme')

    # ── 2. Meinungsstärke: Diff-Histogramm (eigene − IMDB) ───────
    ax = axes[0, 1]
    pole = dims.get('meinungsstaerke', {}).get('pole', '')
    diff = (df['user_rating'] - df['imdb_rating']).dropna()
    if len(diff) > 0:
        bins = [x - 0.5 for x in range(-9, 11)]
        ax.hist(diff, bins=bins, color=COLOR, alpha=0.85, edgecolor=BG, zorder=3)
        ax.yaxis.grid(True, color=GRID, linewidth=0.5, alpha=0.6, zorder=0)
        ax.axvline(0, color='white', lw=1.2, ls='--', alpha=0.55, zorder=4)
        ax.axvline(diff.mean(), color='#ffd700', lw=1.8, alpha=0.9, zorder=5)
        ymax2 = ax.get_ylim()[1]
        ax.text(diff.mean() + 0.2, ymax2 * 0.86, f'Ø {diff.mean():+.2f}', color='#ffd700', fontsize=8)
        std_val = diff.std()
        ax.text(0.97, 0.93, f'σ={std_val:.2f}', transform=ax.transAxes,
                color='#aaaaaa', fontsize=7.5, ha='right', va='top')
    style_ax(ax, f'Meinungsstärke  [{pole}]', 'Eigene − IMDB', 'Filme')

    # ── 3. Geschmacksbreite: Genre-Präferenz (Diverging) ─────────
    ax = axes[1, 0]
    pole = dims.get('geschmacksbreite', {}).get('pole', '')
    gdf  = explode_genres(df)
    if not gdf.empty:
        overall_avg = df['user_rating'].mean()
        genre_stats = gdf.groupby('genre').agg(n=('user_rating','count'), avg=('user_rating','mean'))
        genre_stats = genre_stats[genre_stats['n'] >= 5].copy()
        genre_stats['diff'] = genre_stats['avg'] - overall_avg
        genre_stats = genre_stats.sort_values('diff').tail(12)  # top 12 by diff
        colors = [COLOR_P if v >= 0 else COLOR_N for v in genre_stats['diff']]
        ax.barh(range(len(genre_stats)), genre_stats['diff'].values, color=colors, alpha=0.85, zorder=3)
        ax.xaxis.grid(True, color=GRID, linewidth=0.5, alpha=0.6, zorder=0)
        ax.set_yticks(range(len(genre_stats)))
        ax.set_yticklabels(genre_stats.index, fontsize=7.5, color=TEXT)
        ax.axvline(0, color='white', lw=1, alpha=0.5, zorder=4)
    style_ax(ax, f'Geschmacksbreite  [{pole}]', 'Diff. zu eigenem Ø', '')

    # ── 4. Epoche: Filme nach Jahrzehnt ──────────────────────────
    ax = axes[1, 1]
    pole = dims.get('epoche', {}).get('pole', '')
    if 'year' in df.columns:
        years = df['year'].dropna()
        decades = (years // 10 * 10).astype(int)
        dc = decades.value_counts().sort_index()
        ax.bar(dc.index, dc.values, width=8.5, color=COLOR, alpha=0.85, zorder=3)
        ax.yaxis.grid(True, color=GRID, linewidth=0.5, alpha=0.6, zorder=0)
        median_y = int(years.median())
        ax.axvline(median_y, color='white', lw=1.5, ls='--', alpha=0.85, zorder=5)
        ax.text(median_y + 2, dc.values.max() * 0.9, 'Median\n' + str(median_y),
                color='white', fontsize=7.5)
        ax.set_xticks(dc.index)
        ax.set_xticklabels([f"{d}s" for d in dc.index], rotation=40, ha='right', fontsize=7)
    style_ax(ax, f'Lieblingsepoche  [{pole}]', '', 'Filme')

    plt.suptitle(f'Profil: {name}', color=TEXT, fontsize=12, fontweight='bold', y=1.01)
    plt.tight_layout(h_pad=2.5, w_pad=2.5)
    fig.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close(fig)
    print(f'  Detail-Charts gespeichert: {out_path}')


def save_radar_chart(name, dims, out_path):
    """
    Radar-Chart der 4 Persönlichkeitsdimensionen.
    Jede Achse zeigt einen normalisierten Score 0–1, wobei:
      - Bewertungsstil:   0 = sehr streng, 1 = sehr mild
      - Meinungsstärke:  0 = Diplomat, 1 = Polarisierer
      - Geschmacksbreite: 0 = Spezialist, 1 = Omnivore
      - Lieblingsepoche:  0 = Klassiker (1960), 1 = Zeitgeist (2025)
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    # Dimension → normierter Score [0, 1]
    def norm_score(key, dims):
        if key not in dims:
            return 0.5
        score = dims[key]['score']
        if key == 'bewertungsstil':
            # score ist Δ(user - imdb), Bereich ca. -3 bis +3
            # streng (negativ) = außen (1), mild (positiv) = innen (0)
            return max(0.0, min(1.0, (-score + 3) / 6))
        elif key == 'meinungsstaerke':
            # score ist MSE(user−IMDB), Bereich ca. 0 bis 9 (RMSE≈3 = extreme Abweichung)
            return max(0.0, min(1.0, score / 9.0))
        elif key == 'geschmacksbreite':
            # score ist normierte Entropie [0, 1]
            return max(0.0, min(1.0, score))
        elif key == 'epoche':
            # score ist Medianjahr, Bereich 1960–2025
            # umgekehrt: außen = Klassiker (altes Kino), innen = Zeitgeist
            return max(0.0, min(1.0, 1.0 - (score - 1960) / 65))
        elif key == 'publikum':
            # score: Blockbuster-adj − Arthouse-adj, Bereich ca. -2 bis +2
            # umgekehrt: außen = Arthouse, innen = Blockbuster
            return max(0.0, min(1.0, 1.0 - (score + 2) / 4))
        return 0.5

    _dim_cfg = [
        ('bewertungsstil',   'Streng\n(Bewertungsstil)',   'Mild'),
        ('meinungsstaerke',  'Polarisierer\n(Meinung)',      'Diplomat'),
        ('geschmacksbreite', 'Omnivore\n(Geschmack)',       'Spezialist'),
        ('epoche',           'Klassiker\n(Epoche)',           'Zeitgeist'),
        ('publikum',         'Arthouse\n(Publikum)',         'Blockbuster'),
    ]
    _dim_cfg = [(k, lo, hi) for k, lo, hi in _dim_cfg if k in dims]
    labels        = [lo for _, lo, _ in _dim_cfg]
    counter_labels_dyn = [hi for _, _, hi in _dim_cfg]
    scores = [norm_score(k, dims) for k, _, _ in _dim_cfg]

    n = len(labels)
    angles = [i * 2 * 3.14159265 / n for i in range(n)] + [0]
    scores_plot = scores + [scores[0]]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))

    for r in [0.25, 0.5, 0.75, 1.0]:
        ax.plot(angles, [r] * (n + 1), color='grey', lw=0.5, alpha=0.4)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, size=10)
    ax.set_yticklabels([])
    ax.set_ylim(0, 1)

    for angle, clabel in zip(angles[:-1], counter_labels_dyn):
        ax.text(angle, 0.08, clabel, ha='center', va='center',
                fontsize=10, color='grey')

    ax.fill(angles, scores_plot, color='#e84545', alpha=0.25)
    ax.plot(angles, scores_plot, color='#e84545', lw=2.5)
    ax.scatter(angles[:-1], scores, color='#e84545', s=60, zorder=5)

    ax.set_title(f'Filmpersoenlichkeit: {name}', size=13, pad=20, fontweight='bold')
    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f'  Radar-Chart gespeichert: {out_path}')


# ─────────────────────────────────────────────────────────────────
# 7. REPORT AUSGABE
# ─────────────────────────────────────────────────────────────────


def print_report(name, df, dims, bonus, genre_ach, insider, progressive, topflop, verbose=False):
    sep = '=' * 62
    print(f'\n{sep}')
    print(f'  FILMPERSOENLICHKEIT: {name.upper()}')
    print(f'  Filme bewertet : {len(df)}')
    print(f'  Eigene Bewertung Ø : {df["user_rating"].mean():.2f}')
    print(f'  IMDB-Schnitt der gesehenen Filme Ø : {df["imdb_rating"].mean():.2f}')
    print(f'  Differenz (eigene - IMDB) Ø : {(df["user_rating"] - df["imdb_rating"]).mean():+.2f}')
    print(f'  Streuung eigene Bewertungen (std) : {df["user_rating"].std():.2f}')
    if "year" in df.columns:
        print(f'  Median-Erscheinungsjahr : {int(df["year"].median())}')
    print(f'{sep}')

    print('\n-- PERSOENLICHKEITSPROFIL ' + '-' * 35)
    dim_order = ['bewertungsstil', 'meinungsstaerke', 'geschmacksbreite', 'epoche']
    for key in dim_order:
        if key in dims:
            d = dims[key]
            print(f'  {d["emoji"]} {d["label"]:20s}  [{d["pole"]:13s}]')
            print(f'    -> {d["desc"]}')
            if verbose:
                print(f'       (Rohwert: {d["score"]:.3f})')

    all_ach = progressive + bonus + genre_ach + insider
    if all_ach:
        print(f'\n-- ACHIEVEMENTS ({len(all_ach)}) ' + '-' * 40)
        for a in all_ach:
            print(f'  {a["emoji"]}  {a["name"]}')
            print(f'    -> {a["desc"]}')
    else:
        print('\n  Keine Achievements freigeschaltet.')

    if 'genre_all' in topflop and not topflop['genre_all'].empty:
        bias = topflop.get('overall_bias', 0.0)
        print(f'\n-- ALLE GENRES  (Gesamtbias: {bias:+.2f} | adj = vs_imdb - bias) ' + '-' * 10)
        print(f'  {"Genre":<18s}  {"Ø eigen":>7}  {"Ø IMDB":>7}  {"vs_IMDB":>8}  {"adj":>7}  n')
        print('  ' + '-' * 58)
        for g, row in topflop['genre_all'].iterrows():
            marker = ' ▲' if row['adj'] >= 0.5 else (' ▼' if row['adj'] <= -0.5 else '  ')
            print(f'  {g:<18s}  {row["user_avg"]:>7.2f}  {row["imdb_avg"]:>7.2f}  {row["vs_imdb"]:>+8.2f}  {row["adj"]:>+7.2f}  {int(row["n"])}{marker}')

    if 'dir_top' in topflop and not topflop['dir_top'].empty:
        print(f'\n-- LIEBLINGSREGISSEURE ' + '-' * 38)
        for d, row in topflop['dir_top'].iterrows():
            print(f'  {d:30s}  Ø {row["user_avg"]:.1f}  (vs. IMDB {row["vs_imdb"]:+.1f}, n={int(row["n"])})')
            if verbose and "directors" in df.columns:
                films = df[df["directors"].str.contains(d, na=False)][["title","year","user_rating","imdb_rating"]].sort_values("user_rating", ascending=False)
                for _, fr in films.iterrows():
                    print(f'       {str(fr.get("title",""))[:35]:35s}  {fr["year"] if fr["year"]==fr["year"] else "?":4}  eigene:{fr["user_rating"]:.0f}  imdb:{fr["imdb_rating"]:.1f}')
        print(f'\n-- SCHLECHTESTER SCHNITT ' + '-' * 37)
        for d, row in topflop['dir_flop'].iterrows():
            print(f'  {d:30s}  Ø {row["user_avg"]:.1f}  (vs. IMDB {row["vs_imdb"]:+.1f}, n={int(row["n"])})')
            if verbose and "directors" in df.columns:
                films = df[df["directors"].str.contains(d, na=False)][["title","year","user_rating","imdb_rating"]].sort_values("user_rating", ascending=False)
                for _, fr in films.iterrows():
                    print(f'       {str(fr.get("title",""))[:35]:35s}  {fr["year"] if fr["year"]==fr["year"] else "?":4}  eigene:{fr["user_rating"]:.0f}  imdb:{fr["imdb_rating"]:.1f}')

    if verbose and "directors" in df.columns:
        print(f'\n-- ALLE REGISSEURE (>= 5 Filme) ' + '-' * 29)
        dir_rows = []
        for _, row in df.iterrows():
            if not isinstance(row.get("directors"), str): continue
            for d in [x.strip() for x in row["directors"].split(",")]:
                if d: dir_rows.append({"director": d, "user_rating": row["user_rating"], "imdb_rating": row["imdb_rating"]})
        if dir_rows:
            import pandas as _pd
            ddf = _pd.DataFrame(dir_rows)
            dstats = ddf.groupby("director").agg(n=("user_rating","count"), user_avg=("user_rating","mean"), imdb_avg=("imdb_rating","mean")).round(2)
            dstats["vs_imdb"] = (dstats["user_avg"] - dstats["imdb_avg"]).round(2)
            dstats = dstats[dstats["n"] >= 5].sort_values("user_avg", ascending=False)
            for d, row in dstats.iterrows():
                print(f'  {d:30s}  Ø {row["user_avg"]:.1f}  (vs. IMDB {row["vs_imdb"]:+.1f}, n={int(row["n"])})')

    print(f'\n{sep}\n')


def main():
    import sys
    args = [a for a in sys.argv[1:] if a != '--verbose']
    verbose = '--verbose' in sys.argv

    if len(args) < 1:
        print('Verwendung: python film_personality.py ratings.csv [geburtsjahr] [name] [--verbose]')
        print('  ratings.csv  = IMDB-Export (CSV)')
        print('  geburtsjahr  = z.B. 1996  (optional)')
        print('  name         = z.B. Seb   (optional)')
        print('  --verbose    = zeigt alle Rohwerte und Einzeldatenpunkte')
        sys.exit(0)

    csv_path   = args[0]
    birth_year = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    name       = args[2] if len(args) > 2 else 'User'

    if not os.path.exists(csv_path):
        print(f'Fehler: Datei nicht gefunden: {csv_path}')
        sys.exit(1)

    print('\nLade Daten...')
    df, df_raw = load_imdb_export(csv_path)

    script_dir = os.path.dirname(os.path.abspath(csv_path))
    if not os.path.exists(os.path.join(script_dir, 'david_ratings.csv')) and \
       not os.path.exists(os.path.join(script_dir, 'blockbusters_1960_2025.csv')):
        script_dir = os.path.dirname(os.path.abspath(__file__))
    david_df, robert_df = load_david_robert(script_dir)

    print('Berechne Profil...\n')
    dims        = compute_dimensions(df)
    bonus       = compute_bonus_achievements(df, birth_year, david_df, robert_df)
    genre_ach   = compute_genre_achievements(df)
    insider     = compute_insider_achievements(df, df_raw)
    progressive = compute_progressive_achievements(df_raw)
    topflop     = compute_top_flop(df)

    out_dir    = os.path.dirname(os.path.abspath(__file__))
    radar_path = os.path.join(out_dir, f'radar_{name.lower()}.png')
    save_radar_chart(name, dims, radar_path)

    print_report(name, df, dims, bonus, genre_ach, insider, progressive, topflop, verbose)


if __name__ == '__main__':
    main()
