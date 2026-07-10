"""
Filmbuddy — Community-Vergleich für ZWPUS Film Personality Test
================================================================

Speichert Nutzerratings (opt-in) in Supabase und findet den
nächsten Filmgeschmack-Match (Filmbuddy) sowie den größten
Kontrast (Frenemy).

Benötigt in .streamlit/secrets.toml:
  SUPABASE_URL = "https://xxxxx.supabase.co"
  SUPABASE_KEY = "eyJ..."

Schema: supabase_schema.sql ausführen (einmalig im Supabase SQL Editor).
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional


# ── Client ────────────────────────────────────────────────────────

def _get_client():
    """Gibt initialisierter Supabase-Client zurück oder None."""
    try:
        import streamlit as st
        from supabase import create_client
        url = st.secrets.get('SUPABASE_URL', '')
        key = st.secrets.get('SUPABASE_KEY', '')
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception:
        return None


def is_available() -> bool:
    """True wenn Supabase konfiguriert ist."""
    return _get_client() is not None


# ── Speichern ─────────────────────────────────────────────────────

def save_user_data(display_name: str, df: pd.DataFrame, achievements: list, dims: dict | None = None) -> Optional[str]:
    """
    Speichert / aktualisiert Nutzerratings und Achievements in Supabase.
    Gibt user_id (UUID-String) zurück oder None bei Fehler.

    df muss enthalten: title_norm, year, user_rating
    achievements: kombinierte Liste aller compute_*-Ergebnisse
    """
    client = _get_client()
    if client is None:
        return None

    try:
        now = datetime.now(timezone.utc).isoformat()

        # Nutzer anlegen oder aktualisieren (upsert auf display_name)
        import json as _json
        _base = {
            'display_name': display_name.strip(),
            'film_count':   int(len(df)),
            'last_upload':  now,
        }
        _dims_json = _json.dumps(
            {k: round(float(v['score']), 4) for k, v in dims.items() if 'score' in v}
        ) if dims else None

        # Versuche erst mit dimensions_json (Spalte muss existieren).
        # Falls die Spalte fehlt, falle auf base-Upsert zurück.
        try:
            _upsert = dict(_base)
            if _dims_json:
                _upsert['dimensions_json'] = _dims_json
            result = client.table('fb_users').upsert(
                _upsert, on_conflict='display_name'
            ).execute()
        except Exception:
            result = client.table('fb_users').upsert(
                _base, on_conflict='display_name'
            ).execute()
        user_id = result.data[0]['id']

        # ── Ratings ───────────────────────────────────────────────
        # Altes löschen, komplett neu schreiben (sauberster Re-Upload)
        client.table('fb_ratings').delete().eq('user_id', user_id).execute()

        seen_keys: set[tuple] = set()
        rows = []
        for _, row in df[['title_norm', 'year', 'user_rating']].iterrows():
            if pd.isna(row['user_rating']):
                continue
            yr = int(row['year']) if pd.notna(row.get('year')) else 0
            key = (str(row['title_norm']), yr)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rows.append({
                'user_id':     user_id,
                'title_norm':  str(row['title_norm']),
                'year':        yr,
                'user_rating': round(float(row['user_rating']), 2),
            })

        # Batch-Insert a 500 (Supabase-Limit)
        for i in range(0, len(rows), 500):
            client.table('fb_ratings').insert(rows[i:i + 500]).execute()

        # ── Achievements ──────────────────────────────────────────
        client.table('fb_achievements').delete().eq('user_id', user_id).execute()
        if achievements:
            ach_rows = [
                {
                    'user_id': user_id,
                    'key':     a.get('name', '')[:100],
                    'name':    a.get('name', ''),
                    'emoji':   a.get('emoji', ''),
                }
                for a in achievements
                if a.get('name')
            ]
            if ach_rows:
                client.table('fb_achievements').insert(ach_rows).execute()

        print(f'  Filmbuddy: {len(rows)} Ratings gespeichert für "{display_name}" ({user_id[:8]}...)')
        return user_id

    except Exception as e:
        print(f'  Filmbuddy save_user_data FEHLER: {e}')
        return None


# ── Matching ──────────────────────────────────────────────────────

def find_buddy(user_id: str, df: pd.DataFrame) -> dict:
    """
    Findet Filmbuddy (höchste Pearson-Korrelation) und Frenemy
    (niedrigste Korrelation) für den gegebenen User.

    Alle anderen Ratings werden in einer einzigen DB-Query geholt
    und in Python verglichen — effizient auch für mehrere hundert User.

    Gibt zurück:
      {
        'buddy':       {name, corr, n, top_common, top_diff} | None,
        'frenemy':     {name, corr, n, top_common, top_diff} | None,
        'total_users': int,  # Anzahl User mit genug Überschneidungen
      }
    """
    client = _get_client()
    if client is None:
        return {}

    try:
        # Eigene Ratings als Dict: "title_norm|year" → rating
        # Beide Keys (original_title + localized title) damit LB-Titel matchen
        my_ratings: dict[str, float] = {}
        for _, row in df.dropna(subset=['user_rating']).iterrows():
            year = int(row['year']) if pd.notna(row.get('year')) else 0
            rating = float(row['user_rating'])
            key1 = f"{row['title_norm']}|{year}"
            my_ratings[key1] = rating
            # Alternativer Key (lokalisierter Titel, z.B. "parasite" für "기생충")
            alt = row.get('title_alt_norm')
            if pd.notna(alt) and str(alt) and str(alt) != str(row['title_norm']):
                key2 = f"{alt}|{year}"
                if key2 not in my_ratings:
                    my_ratings[key2] = rating
        my_keys = set(my_ratings)

        # Genre-Lookup: key → [genre, …] (aus user-df, nur IMDB/TMDB-Uploads haben Genres)
        _genre_lookup: dict[str, list] = {}
        if 'genres' in df.columns:
            for _, _gr in df.dropna(subset=['title_norm']).iterrows():
                _yr  = int(_gr['year']) if pd.notna(_gr.get('year')) else 0
                _raw = _gr.get('genres', '')
                if not pd.notna(_raw) or not str(_raw).strip():
                    continue
                _gl = [g.strip() for g in str(_raw).split(',') if g.strip()]
                _genre_lookup[f"{_gr['title_norm']}|{_yr}"] = _gl
                _alt = _gr.get('title_alt_norm')
                if pd.notna(_alt) and str(_alt) != str(_gr['title_norm']):
                    _genre_lookup[f"{_alt}|{_yr}"] = _gl


        # Alle anderen User laden, dann pro User separate Query
        # (Supabase hat serverseitiges max_rows=1000 pro Request —
        #  ein Query für alle User würde nur die ersten 1000 Zeilen liefern)
        users_res = client.table('fb_users').select('id, display_name, dimensions_json').neq('id', user_id).execute()
        if not users_res.data:
            return {'buddy': None, 'frenemy': None, 'total_users': 0,
                    'debug_per_user': {}}

        user_names     = {u['id']: u['display_name']            for u in users_res.data}
        user_dims_json = {u['id']: u.get('dimensions_json')     for u in users_res.data}

        # Pro User paginierte Queries (PostgREST-Serverlimit = 1000 Zeilen/Request)
        _PAGE = 1000
        other: dict[str, dict[str, float]] = {}
        for u in users_res.data:
            uid_other = u['id']
            ratings: dict[str, float] = {}
            offset = 0
            while True:
                page = (
                    client.table('fb_ratings')
                    .select('title_norm, year, user_rating')
                    .eq('user_id', uid_other)
                    .range(offset, offset + _PAGE - 1)
                    .execute()
                )
                for r in page.data:
                    ratings[f"{r['title_norm']}|{r['year']}"] = float(r['user_rating'])
                if len(page.data) < _PAGE:
                    break   # letzte Seite erreicht
                offset += _PAGE
            other[uid_other] = ratings

        # Korrelation für jeden anderen User berechnen
        results = []
        debug_per_user: dict[str, int] = {}   # name → n_common (alle User, auch < 3)
        for uid, their_ratings in other.items():
            common = my_keys & set(their_ratings)
            uname = user_names.get(uid, '???')
            debug_per_user[uname] = len(common)
            if len(common) < 3:
                continue

            # ── Genre-Overlap ──────────────────────────────────────
            _gc: dict[str, float] = {}
            for _k in common:
                _w = min(my_ratings[_k], their_ratings[_k])
                for _g in _genre_lookup.get(_k, []):
                    _gc[_g] = _gc.get(_g, 0.0) + _w
            top_genres = sorted(_gc.items(), key=lambda x: x[1], reverse=True)[:3]

            # ── Unseen Gem ─────────────────────────────────────────
            # Bester Film des Buddys den der User noch nicht bewertet hat
            _unseen = sorted(
                [(k.split('|')[0], their_ratings[k])
                 for k in their_ratings
                 if k not in my_keys and their_ratings[k] >= 8.0],
                key=lambda x: x[1], reverse=True
            )
            unseen_gem = _unseen[0] if _unseen else None

            mine   = np.array([my_ratings[k]    for k in common])
            theirs = np.array([their_ratings[k] for k in common])

            if mine.std() < 0.01 or theirs.std() < 0.01:
                continue

            corr = float(np.corrcoef(mine, theirs)[0, 1])
            if np.isnan(corr):
                continue

            # Beste Übereinstimmungen: beide geben ≥ 7, als Tupel mit Ratings
            top_agree = [
                (k.split('|')[0], my_ratings[k], their_ratings[k])
                for k in sorted(common,
                                key=lambda k: min(my_ratings[k], their_ratings[k]),
                                reverse=True)
                if my_ratings[k] >= 7 and their_ratings[k] >= 7
            ][:3]

            # Größte Abweichungen
            top_diff = [
                (k.split('|')[0], my_ratings[k], their_ratings[k])
                for k in sorted(common,
                                key=lambda k: abs(my_ratings[k] - their_ratings[k]),
                                reverse=True)
            ][:3]

            # Deal Breaker: Differenz ≥ 5 Punkte
            dealbreaker = [
                (k.split('|')[0], my_ratings[k], their_ratings[k])
                for k in sorted(common,
                                key=lambda k: abs(my_ratings[k] - their_ratings[k]),
                                reverse=True)
                if abs(my_ratings[k] - their_ratings[k]) >= 5
            ][:3]

            # Alle Buddy-Ratings für Verteilungs-Histogram
            buddy_all_ratings = list(their_ratings.values())

            import json as _json
            _raw_dj = user_dims_json.get(uid)
            results.append({
                'name':            user_names.get(uid, '???'),
                'corr':            round(corr, 3),
                'n':               len(common),
                'top_agree':       top_agree,
                'top_diff':           top_diff,
                'dealbreaker':        dealbreaker,
                'buddy_all_ratings':  buddy_all_ratings,
                'buddy_dims_raw':     _json.loads(_raw_dj) if _raw_dj else None,
                'top_genres':         top_genres,
                'kinoabend':          top_agree[0] if top_agree else None,
                'unseen_gem':         unseen_gem,
            })

        if not results:
            return {'buddy': None, 'frenemy': None, 'total_users': 0,
                    'debug_per_user': debug_per_user}

        results.sort(key=lambda x: x['corr'], reverse=True)
        # Frenemy: schlechteste Korrelation — auch wenn nur 1 Person (dann buddy == frenemy)
        return {
            'buddy':          results[0],
            'frenemy':        results[-1],
            'total_users':    len(results),
            'debug_per_user': debug_per_user,
        }

    except Exception as e:
        print(f'  Filmbuddy find_buddy FEHLER: {e}')
        return {}


# ── Seed ──────────────────────────────────────────────────────────

def seed_initial_users(david_path: str, robert_path: str) -> str:
    """
    Befüllt die Datenbank einmalig mit Davids und Roberts Ratings aus den
    lokalen CSV-Dateien (david_ratings.csv / robert_ratings.csv).

    CSV-Format: slug, title, year, rating  (rating auf 0.5–5-Skala → ×2 = 1–10)

    Gibt einen Status-String zurück.
    """
    import re
    import os

    client = _get_client()
    if client is None:
        return '❌ Supabase nicht verbunden.'

    # Gleiche Normalisierung wie normalize_title in film_personality.py
    # (NFKD-Decomposition → ASCII → a-z0-9) damit Supabase-Keys mit
    # dem Upload von Nutzern übereinstimmen
    import unicodedata as _ud
    def _norm(t):
        t = _ud.normalize('NFKD', str(t))
        t = t.encode('ascii', 'ignore').decode()
        t = re.sub(r'[^a-z0-9 ]', '', t.lower())
        return re.sub(r'\s+', ' ', t).strip()

    messages = []
    seeds = [
        ('David',  david_path,  'david_rating'),
        ('Robert', robert_path, 'robert_rating'),
    ]

    for name, path, _ in seeds:
        if not os.path.exists(path):
            messages.append(f'⚠️ {name}: Datei nicht gefunden ({path})')
            continue
        try:
            df = pd.read_csv(path, encoding='utf-8')
            df.columns = [c.strip() for c in df.columns]
            df['rating'] = pd.to_numeric(df['rating'], errors='coerce')
            df = df.dropna(subset=['rating', 'title'])
            df['year'] = pd.to_numeric(df.get('year', 0), errors='coerce').fillna(0).astype(int)
            df['title_norm'] = df['title'].apply(_norm)
            df['user_rating'] = (df['rating'] * 2).round(2)

            now = datetime.now(timezone.utc).isoformat()

            # Nutzer anlegen / aktualisieren
            res = client.table('fb_users').upsert(
                {'display_name': name, 'film_count': len(df), 'last_upload': now},
                on_conflict='display_name'
            ).execute()
            uid = res.data[0]['id']

            # Alte Ratings löschen, neu schreiben
            client.table('fb_ratings').delete().eq('user_id', uid).execute()

            seen: set[tuple] = set()
            rows = []
            for _, row in df.iterrows():
                if row['user_rating'] < 1:
                    continue
                key = (row['title_norm'], int(row['year']))
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    'user_id':     uid,
                    'title_norm':  row['title_norm'],
                    'year':        int(row['year']),
                    'user_rating': float(row['user_rating']),
                })
            for i in range(0, len(rows), 500):
                client.table('fb_ratings').insert(rows[i:i + 500]).execute()

            messages.append(f'✅ {name}: {len(rows)} Ratings gespeichert.')
            print(f'  Seed: {name} → {len(rows)} Ratings ({uid[:8]}...)')

        except Exception as e:
            messages.append(f'❌ {name}: {e}')

    return '\n'.join(messages)


# ── Buddy ohne CSV-Upload ─────────────────────────────────────────

def find_buddy_by_name(display_name: str) -> dict:
    """
    Berechnet Filmbuddy/Frenemy für einen bereits gespeicherten Nutzer —
    ohne dass eine CSV hochgeladen werden muss.
    Gibt dasselbe Format zurück wie find_buddy().
    """
    client = _get_client()
    if client is None:
        return {}
    try:
        # User-ID anhand des Namens
        res = client.table('fb_users').select('id').eq('display_name', display_name.strip()).execute()
        if not res.data:
            return {'error': 'Kein Nutzer mit diesem Namen gefunden.'}
        user_id = res.data[0]['id']

        # Eigene Ratings aus DB laden
        my_res = (
            client.table('fb_ratings')
            .select('title_norm, year, user_rating')
            .eq('user_id', user_id)
            .limit(100_000)
            .execute()
        )
        if not my_res.data:
            return {'error': 'Keine gespeicherten Ratings gefunden.'}

        df_my = pd.DataFrame(my_res.data)
        return find_buddy(user_id, df_my.rename(columns={'user_rating': 'user_rating'}))

    except Exception as e:
        print(f'  Filmbuddy find_buddy_by_name FEHLER: {e}')
        return {}


# ── Community-Stats ───────────────────────────────────────────────

def get_community_stats() -> dict:
    """Gibt einfache Community-Statistiken zurück."""
    client = _get_client()
    if client is None:
        return {}
    try:
        res = client.table('fb_users').select('display_name, film_count, last_upload').execute()
        if not res.data:
            return {}
        total = len(res.data)
        total_films = sum(u['film_count'] for u in res.data)
        return {
            'total_users':  total,
            'total_films':  total_films,
            'users':        res.data,
        }
    except Exception:
        return {}
