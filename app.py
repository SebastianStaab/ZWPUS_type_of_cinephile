"""
Zwei wie Pech & Schwafel — Film Personality Test
Streamlit Web App

Deploy auf Streamlit Community Cloud:
  1. Repo auf GitHub pushen
  2. app.streamlit.io → New app → Repo auswählen
  3. Secrets: TMDB_API_KEY = "dein-key"
"""

import os
import io
import threading as _threading
import streamlit as st
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Lokale Module
import sys
sys.path.insert(0, os.path.dirname(__file__))
from film_personality import (
    detect_and_load, load_david_robert,
    compute_dimensions, compute_bonus_achievements,
    compute_genre_achievements, compute_insider_achievements,
    compute_progressive_achievements, compute_top_flop,
    save_radar_chart, save_comparison_radar, save_single_dimension_chart,
    save_formative_years_chart, compute_formative_years_stats,
)
import filmbuddy as _fb


# ── Cache-Warming (Background) ───────────────────────────────────
_cache_warmed   = False
_cache_warm_lock = _threading.Lock()

def _start_cache_warming(api_key, script_dir, cache_path):
    """
    Startet einmalig einen Background-Thread, der den TMDB-Cache
    mit David- und Robert-Ratings vorwärmt. Non-blocking für den User.
    """
    global _cache_warmed
    with _cache_warm_lock:
        if _cache_warmed or not api_key:
            return
        _cache_warmed = True

    def _warm():
        try:
            from tmdb_enrich import load_cache, enrich_letterboxd, DELAY_WARM
            # Schon warm genug?
            if len(load_cache(cache_path)) > 2000:
                return
            for fname in ['david_ratings.csv', 'robert_ratings.csv']:
                fpath = os.path.join(script_dir, fname)
                if not os.path.exists(fpath):
                    continue
                df_w = pd.read_csv(fpath)
                if 'rating' in df_w.columns:
                    df_w['user_rating'] = df_w['rating'] * 2
                if 'title' not in df_w.columns and len(df_w.columns) > 1:
                    df_w = df_w.rename(columns={df_w.columns[1]: 'title'})
                # DELAY_WARM (1.0s) statt DELAY (0.25s) → kein Rate-Limit-Konflikt
                enrich_letterboxd(df_w, api_key, cache_path=cache_path,
                                  progress_cb=None, api_delay=DELAY_WARM)
        except Exception:
            pass   # Warming-Fehler sind nicht kritisch

    _threading.Thread(target=_warm, daemon=True).start()


# ── Seitenkonfiguration ───────────────────────────────────────────
st.set_page_config(
    page_title='ZWPUS Film Personality',
    page_icon='🎬',
    layout='wide',
)


# ── Sidebar ───────────────────────────────────────────────────────
with st.sidebar:
    st.image('https://img.shields.io/badge/Zwei%20wie%20Pech%20%26%20Schwafel-%F0%9F%8E%AC-red', width='stretch')
    st.title('🎬 Film Personality')
    st.caption('Powered by Letterboxd + IMDB + TMDB')
    st.divider()

    name       = st.text_input('Dein Name', value='')
    birth_year = st.number_input('Geburtsjahr (optional)', min_value=1920,
                                 max_value=2010, value=1995, step=1)
    birth_year = int(birth_year) if birth_year else None

    # ── Filmbuddy-Speichern-Button ────────────────────────────────
    if _fb.is_available():
        _fb_btn_disabled = not (name.strip() and st.session_state.get('main_upload') is not None)
        if st.button('💾 Im Filmbuddy-Pool speichern', disabled=_fb_btn_disabled, key='sb_fb_save'):
            st.session_state['fb_save_triggered'] = True
        if not name.strip():
            st.caption('← Name eingeben um zu speichern')
        elif st.session_state.get('main_upload') is None:
            st.caption('← CSV hochladen um zu speichern')

    st.divider()

    # ── Info-Block: Cache + Pool (kompakt zusammen) ───────────────
    _cache_status = st.empty()   # wird von _update_cache_status befüllt

    if _fb.is_available():
        _sidebar_stats = _fb.get_community_stats()
        if _sidebar_stats and _sidebar_stats.get('total_users', 0) > 0:
            _pool_films = _sidebar_stats.get('total_films') or _sidebar_stats.get('avg_films', '?')
            st.caption(
                f"🤝 **Filmbuddy-Pool:** {_sidebar_stats['total_users']} Nutzer · "
                f"{_pool_films} Filme"
            )
        else:
            st.caption('🤝 **Filmbuddy-Pool:** noch leer')
        # Seed-Button immer anzeigen (wird für Re-Seed nach Normalisierungs-Updates gebraucht)
        if st.button('🌱 David & Robert (neu) eintragen', key='sb_seed_btn'):
            _seed_dir = os.path.dirname(os.path.abspath(__file__))
            with st.spinner('Seed läuft...'):
                _seed_msg = _fb.seed_initial_users(
                    os.path.join(_seed_dir, 'david_ratings.csv'),
                    os.path.join(_seed_dir, 'robert_ratings.csv'),
                )
            st.session_state['seed_msg'] = _seed_msg
            st.rerun()
        if st.session_state.get('seed_msg'):
            st.caption(st.session_state.pop('seed_msg'))

def _update_cache_status(done=None, total=None):
    """Zeigt Enrichment-Fortschritt oder Cache-Größe im Sidebar."""
    import json as _j
    _cpath_app = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmdb_cache.json')
    _cpath_tmp = '/tmp/tmdb_cache.json'
    _n = 0
    for _cp in [_cpath_app, _cpath_tmp]:
        try:
            _n = max(_n, len(_j.load(open(_cp))))
        except Exception:
            pass
    if done is not None and total:
        _cache_status.caption(f'🔄 TMDB lädt: {done}/{total} Filme… (Cache: {_n})')
    elif _n > 0:
        _cache_status.caption(f'📦 TMDB-Cache: {_n} Filme gecacht')
    elif _cache_warmed:
        _cache_status.caption('⏳ Cache wird vorbereitet...')
    else:
        _cache_status.caption('📦 TMDB-Cache: leer')

_update_cache_status()

# ── API-Key aus Secrets (kein manuelles Eingabefeld mehr) ────────
try:
    api_key = (st.secrets['TMDB_API_KEY'] or '').strip()
except Exception:
    api_key = ''

_app_dir   = os.path.dirname(os.path.abspath(__file__))
_cache_app = os.path.join(_app_dir, 'tmdb_cache.json')
_cache_tmp = '/tmp/tmdb_cache.json'
try:
    import tempfile as _tf
    _tf_fd, _tf_path = _tf.mkstemp(dir=_app_dir)
    os.close(_tf_fd)
    os.unlink(_tf_path)
    cache_path = _cache_app
except Exception:
    cache_path = _cache_tmp

# Cache-Warming: startet beim App-Load (vor File-Upload), damit keine Konkurrenz
_start_cache_warming(api_key, _app_dir, cache_path)

# ── Hauptbereich ──────────────────────────────────────────────────
st.title('🎬 Zwei wie Pech & Schwafel')
st.subheader('Dein Film-Persönlichkeitstest')

uploaded = st.file_uploader(
    'Ratings-CSV hochladen (Letterboxd oder IMDB Export)',
    type=['csv'],
    key='main_upload',
    help='Letterboxd: Einstellungen → Daten → Export | IMDB: imdb.com → Deine Ratings → Export'
)

if not uploaded:
    st.session_state.pop('fb_match',   None)
    st.session_state.pop('fb_save_ok', None)
    st.info('⬆️ Lade deine Ratings-CSV hoch um loszulegen.')
    with st.expander('Wie funktioniert das?'):
        st.markdown(
            '### Letterboxd-Export\n'
            '1. letterboxd.com → Profil → **Einstellungen** → **Daten** → "Export Your Data"\n'
            '2. ZIP öffnen → `ratings.csv` hochladen\n\n'
            '### IMDB-Export\n'
            '1. imdb.com → Profil-Icon → **Your ratings** → `...` → **Export**\n'
            '2. CSV direkt hochladen — Genres und Regisseure sind bereits enthalten\n'
        )

    st.stop()

# ── Daten laden ───────────────────────────────────────────────────

# Datei in temporären Pfad schreiben
tmp_path = '/tmp/ratings_upload.csv'
with open(tmp_path, 'wb') as f:
    f.write(uploaded.read())

# Progressbar für TMDB-Anreicherung
_prog_bar  = st.empty()
_prog_text = st.empty()

def _tmdb_progress(done, total):
    pct = done / total if total else 0
    _prog_bar.progress(pct)
    _prog_text.caption(f'🎬 TMDB-Anreicherung: {done}/{total} Filme geladen…')
    _update_cache_status(done, total)
    if done == total:
        _prog_bar.empty()
        _prog_text.empty()
        _update_cache_status()  # finale Cache-Größe

# ── TMDB API Quick-Test ──────────────────────────────────────────
_is_lb_quick = False
try:
    _quick_cols = pd.read_csv(tmp_path, nrows=0).columns.tolist()
    _is_lb_quick = 'Name' in _quick_cols and 'Letterboxd URI' in _quick_cols
except Exception:
    pass

_debug_api_lines = []
if _is_lb_quick and api_key:
    try:
        import requests as _req
        _tr = _req.get('https://api.themoviedb.org/3/search/movie',
                       params={'api_key': api_key.strip(), 'query': 'Pulp Fiction'}, timeout=8)
        _debug_api_lines.append(f'API-Test "Pulp Fiction": HTTP {_tr.status_code} — {len(_tr.json().get("results", []))} Treffer')
        try:
            _lbdf_test = pd.read_csv(tmp_path, nrows=2)
            _t1 = str(_lbdf_test['Name'].iloc[0]) if 'Name' in _lbdf_test.columns else '?'
            _y1 = _lbdf_test['Year'].iloc[0] if 'Year' in _lbdf_test.columns else None
            _params1 = {'api_key': api_key.strip(), 'query': _t1}
            if _y1 and str(_y1) not in ('nan', '0'):
                _params1['year'] = int(_y1)
            _tr2 = _req.get('https://api.themoviedb.org/3/search/movie', params=_params1, timeout=8)
            _debug_api_lines.append(f'API-Test "{_t1}" ({_y1}): HTTP {_tr2.status_code} — {len(_tr2.json().get("results", []))} Treffer')
        except Exception as _te2:
            _debug_api_lines.append(f'LB-Film-Test FEHLER: {_te2}')
    except Exception as _te:
        _debug_api_lines.append(f'API-Test FEHLER: {_te}')

with st.spinner('Lade Ratings...'):
    try:
        df, df_raw = detect_and_load(
            tmp_path,
            api_key=api_key if api_key else None,
            cache_path=cache_path,
            progress_cb=_tmdb_progress,
        )
    except Exception as e:
        st.error(f'Fehler beim Laden: {e}')
        st.stop()

script_dir = os.path.dirname(os.path.abspath(__file__))
david_df, robert_df = load_david_robert(script_dir)

# Warnung für Letterboxd-Exporte ohne IMDB-Daten
_is_lb   = 'lb_rating' in df_raw.columns
_no_imdb = 'imdb_rating' not in df.columns or df['imdb_rating'].isna().all()


if _is_lb and _no_imdb:
    if not api_key:
        st.warning(
            '**Kein TMDB-API-Key gesetzt** — Genres, Regisseure und IMDB-Vergleiche fehlen. '
            'Dadurch fehlen: Geschmacksbreite, Publikumsgeschmack, Genre-Achievements, '
            'Regisseur-Analyse und Abweichungsvergleiche. '
            'API-Key in der Sidebar eingeben (kostenlos auf themoviedb.org) '
            'und CSV erneut hochladen.',
            icon='🔑'
        )
    else:
        st.warning(
            '**TMDB-Anreicherung lieferte keine Daten** — möglicherweise sind die Filme noch nicht im '
            'Cache und die API hat beim Laden Probleme gehabt. '
            'Seite neu laden und CSV erneut hochladen. '
            f'(API-Key ist gesetzt, {len(df)} Filme verarbeitet)',
            icon='⚠️'
        )



# ── Profil berechnen ──────────────────────────────────────────────
# Datenquelle bestimmen: LB-Upload → TMDB-angereichert, IMDB-Upload → IMDB-Daten
_rating_source = 'TMDB' if 'lb_rating' in df_raw.columns else 'IMDB'

with st.spinner('Berechne Profil...'):
    dims        = compute_dimensions(df, rating_source=_rating_source)
    bonus       = compute_bonus_achievements(df, birth_year, david_df, robert_df, rating_source=_rating_source)
    genre_ach   = compute_genre_achievements(df)
    insider     = compute_insider_achievements(df, df_raw)
    progressive = compute_progressive_achievements(df_raw)
    topflop     = compute_top_flop(df)

display_name = name.strip() if name.strip() else 'Anonym'

# Formative-Jahre-Bias + Signifikanz
formative_stats = compute_formative_years_stats(df, birth_year)

# ── Filmbuddy: Save-Trigger abarbeiten ───────────────────────────
if _fb.is_available() and st.session_state.get('fb_save_triggered') and name.strip():
    st.session_state.pop('fb_save_triggered', None)
    _all_ach = bonus + genre_ach + insider + progressive
    with st.spinner('Speichere im Filmbuddy-Pool…'):
        _uid = _fb.save_user_data(name.strip(), df, _all_ach, dims=dims)
    if _uid:
        st.session_state['fb_save_ok'] = len(df)
        with st.spinner('Suche Filmbuddy & Frenemy…'):
            st.session_state['fb_match'] = _fb.find_buddy(_uid, df)
    else:
        st.session_state.pop('fb_match', None)
        st.error('Filmbuddy: Speichern fehlgeschlagen — Supabase nicht erreichbar.')

# ── Filmbuddy-Kapitel (ganz oben) ────────────────────────────────
if _fb.is_available() and st.session_state.get('fb_match') is not None:
    st.divider()
    st.subheader('🤝 Filmbuddy')
    if st.session_state.get('fb_save_ok'):
        st.success(f"✅ {st.session_state.pop('fb_save_ok')} Ratings gespeichert!")
    _match = st.session_state['fb_match']
    if not _match or _match.get('total_users', 0) == 0:
        st.info('Noch zu wenige Nutzer mit Überschneidungen. Schick den Link an die Community! 🎬')
        _dpu0 = _match.get('debug_per_user', {}) if _match else {}
        if _dpu0:
            with st.expander('🔍 Gemeinsame Filme pro Nutzer', expanded=True):
                for _uname, _n in sorted(_dpu0.items(), key=lambda x: x[1], reverse=True):
                    _flag = ' ✅' if _n >= 3 else ' ⚠️ (< 3, kein Match)'
                    st.write(f'**{_uname}**: {_n} gemeinsame Filme{_flag}')
    else:
        buddy   = _match.get('buddy')
        frenemy = _match.get('frenemy')
        _same   = buddy and frenemy and buddy['name'] == frenemy['name']
        # Gemeinsamer Radar: Du + Buddy + Frenemy
        _others_radar = []
        if buddy:
            _bd = buddy.get('buddy_dims_raw') or buddy.get('computed_buddy_dims')
            if _bd:
                _others_radar.append((buddy['name'], _bd, '#4caf50', '--'))
        if frenemy and not _same:
            _fd = frenemy.get('buddy_dims_raw') or frenemy.get('computed_buddy_dims')
            if _fd:
                _others_radar.append((frenemy['name'], _fd, '#e84545', ':'))
        if _others_radar:
            _tri_path = '/tmp/triple_radar.png'
            if save_comparison_radar(display_name, dims, _others_radar, _tri_path):
                st.image(_tri_path, width='stretch')

        _bcol, _fcol = st.columns(2)

        def _corr_to_pct(r):
            return int(round((r + 1) / 2 * 100))

        def _render_table(rows, col_me, col_them):
            if not rows:
                return
            _clean = []
            for r in rows:
                if isinstance(r, (list, tuple)) and len(r) >= 3:
                    _clean.append((str(r[0]).title(), f'{float(r[1]):.0f}', f'{float(r[2]):.0f}'))
                elif isinstance(r, str):
                    _clean.append((r.title(), '—', '—'))
            if not _clean:
                return
            _df_t = pd.DataFrame(_clean, columns=['Film', col_me, col_them])
            st.dataframe(_df_t, width='stretch', hide_index=True)

        def _rating_histogram(my_ratings_all, buddy_ratings_all, buddy_name):
            """Verteilung: Wie streng bewertet ihr beide generell?"""
            if not buddy_ratings_all:
                return
            import numpy as np
            _bins = np.arange(0.5, 11.5, 1)
            _fig, _ax = plt.subplots(figsize=(5, 2.8))
            _fig.patch.set_facecolor('#0e1117')
            _ax.set_facecolor('#0e1117')
            _ax.hist(my_ratings_all, bins=_bins, alpha=0.65, color='#e84545',
                     label='Du', density=True)
            _ax.hist(buddy_ratings_all, bins=_bins, alpha=0.55, color='#4fc3f7',
                     label=buddy_name, density=True)
            _ax.set_xlabel('Bewertung', color='white', fontsize=9)
            _ax.set_ylabel('Anteil', color='white', fontsize=9)
            _ax.set_xlim(0.5, 10.5)
            _ax.tick_params(colors='white', labelsize=8)
            for spine in _ax.spines.values():
                spine.set_edgecolor('#333')
            _ax.legend(fontsize=8, framealpha=0.3, labelcolor='white', facecolor='#222')
            _ax.set_title('Bewertungsstil', color='white', fontsize=9)
            plt.tight_layout()
            st.pyplot(_fig, width='stretch')
            plt.close(_fig)

        def _render_person(person, bg, emoji, label, show_agree=True):
            _pct = _corr_to_pct(person['corr'])
            st.markdown(
                f"<div style='background:{bg};border-radius:12px;padding:14px 18px;margin-bottom:8px'>"
                f"<h3 style='margin:0 0 2px 0'>{emoji} {label}</h3>"
                f"<p style='font-size:1.4em;font-weight:bold;margin:0'>{person['name']}</p>"
                f"</div>",
                unsafe_allow_html=True
            )
            _m1, _m2 = st.columns(2)
            _m1.metric('Match-Score', f'{_pct}%',
                       help='(Pearson r + 1) / 2 × 100 — 100% = identischer Geschmack')
            _m2.metric('Gemeinsame Filme', person['n'])

            # Bewertungsverteilung
            _my_all = df['user_rating'].dropna().tolist()
            _rating_histogram(_my_all, person.get('buddy_all_ratings', []), person['name'])

            # Deal Breaker
            if person.get('dealbreaker'):
                st.markdown(f'**💔 Deal Breaker** *(Differenz ≥ 5 Punkte)*')
                _render_table(person['dealbreaker'], 'Du', person['name'])

            # Beide lieben
            if show_agree and person.get('top_agree'):
                st.markdown(f'**🍿 Ihr liebt beide**')
                _render_table(person['top_agree'], 'Du', person['name'])

            # Meinungsverschiedenheiten
            if person.get('top_diff'):
                st.markdown(f'**🔀 Größte Meinungsverschiedenheiten**')
                _render_table(person['top_diff'], 'Du', person['name'])

            # Erster gemeinsamer Kinoabend
            if person.get('kinoabend'):
                _kb = person['kinoabend']
                _kb_title = str(_kb[0]).title() if isinstance(_kb, (list, tuple)) else str(_kb)
                _kb_me    = f"{float(_kb[1]):.0f}" if isinstance(_kb, (list, tuple)) and len(_kb) > 1 else '—'
                _kb_them  = f"{float(_kb[2]):.0f}" if isinstance(_kb, (list, tuple)) and len(_kb) > 2 else '—'
                st.markdown(
                    f"<div style='background:#1a2a3a;border-radius:8px;padding:10px 14px;margin:6px 0'>"
                    f"<span style='font-size:0.8em;color:#aaa'>🎟️ Euer erster gemeinsamer Kinoabend</span><br>"
                    f"<strong>{_kb_title}</strong> &nbsp;"
                    f"<span style='color:#aaa;font-size:0.85em'>Du: {_kb_me} · {person['name']}: {_kb_them}</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )

            # Unseen Gem
            if person.get('unseen_gem'):
                _ug_title  = str(person['unseen_gem'][0]).title()
                _ug_rating = f"{float(person['unseen_gem'][1]):.0f}"
                st.markdown(
                    f"<div style='background:#2a1a3a;border-radius:8px;padding:10px 14px;margin:6px 0'>"
                    f"<span style='font-size:0.8em;color:#aaa'>💎 {person['name']}s Hidden Gem für dich</span><br>"
                    f"<strong>{_ug_title}</strong> &nbsp;"
                    f"<span style='color:#aaa;font-size:0.85em'>{person['name']}: {_ug_rating} · noch nicht von dir bewertet</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )

            # Genre-Overlap
            if person.get('top_genres'):
                _genres_str = ' · '.join(g for g, _ in person['top_genres'])
                st.markdown(
                    f"<div style='margin:6px 0'>"
                    f"<span style='font-size:0.8em;color:#aaa'>🎭 Eure Genre-Schnittmenge</span><br>"
                    f"<strong>{_genres_str}</strong>"
                    f"</div>",
                    unsafe_allow_html=True
                )



        if buddy:
            with _bcol:
                _render_person(buddy, '#1a3a2a', '🎬', 'Dein Filmbuddy', show_agree=True)

        if frenemy:
            with _fcol:
                _render_person(frenemy, '#3a1a1a', '😈', 'Dein Frenemy', show_agree=not _same)
                if _same:
                    st.caption('Noch zu wenige Vergleichspersonen — mit mehr Nutzern bekommst du einen echten Frenemy.')

        # Debug: Gemeinsame Filme pro Nutzer (zusammenklappbar)
        _dpu = _match.get('debug_per_user', {})
        if _dpu:
            with st.expander('🔍 Gemeinsame Filme pro Nutzer', expanded=False):
                for _uname, _n in sorted(_dpu.items(), key=lambda x: x[1], reverse=True):
                    _flag = ' ✅' if _n >= 3 else ' ⚠️ (< 3, kein Match)'
                    st.write(f'**{_uname}**: {_n} gemeinsame Filme{_flag}')

# ── Achievements (ganz oben) ─────────────────────────────────────
all_ach = progressive + bonus + genre_ach + insider
if all_ach:
    st.subheader(f'🏅 Achievements ({len(all_ach)})')
    cols = st.columns(min(len(all_ach), 3))
    for i, a in enumerate(all_ach):
        with cols[i % 3]:
            st.markdown(
                f'<div style="border:1px solid #333;border-radius:8px;padding:12px;margin:4px">' +
                f'<div style="font-size:2em">{a["emoji"]}</div>' +
                f'<b>{a["name"]}</b><br>' +
                f'<small>{a["desc"]}</small>' +
                f'</div>',
                unsafe_allow_html=True
            )

# ── Hauptlayout ───────────────────────────────────────────────────
st.divider()
col_left, col_right = st.columns([1.1, 0.9], gap='large')

# ── RECHTS: Übersicht + Prägende Jahre + Radar ────────────────────
with col_right:
    # Basisdaten
    st.metric('Filme bewertet', len(df))
    m1, m2, m3 = st.columns(3)
    m1.metric('Eigene Ø', f'{df["user_rating"].mean():.2f}')
    if df['imdb_rating'].notna().sum() > 10:
        _bias_val = (df['user_rating'] - df['imdb_rating']).mean()
        m2.metric(f'{_rating_source} Ø', f'{df["imdb_rating"].mean():.2f}')
        m3.metric('Bias', f'{_bias_val:+.2f}')

    # Prägende Jahre
    if formative_stats is not None:
        fs = formative_stats
        st.divider()
        sig_text = '(signifikant ✓)' if fs['significant'] else '(nicht signifikant)'
        _rs = _rating_source
        method_note = f'{_rs}-bereinigt' if fs.get('has_imdb') else 'Rohrating'
        st.markdown(f"**🎞️ Prägende Jahre {fs['form_start']}–{fs['form_end']}** _{method_note}_")
        if fs.get('has_imdb'):
            st.caption(
                f"Bewertest du Filme aus deinen prägenden Jahren ({fs['form_start']}–{fs['form_end']}) "
                f"besser als den Rest? **Bias (roh)** = direkter Vergleich deiner Noten. "
                f"**Bias ({_rs}-bereinigt)** = bereinigt um Qualitätsunterschiede zwischen Ären — "
                f"ältere Filme haben auf {_rs} oft höhere Schnitte, das wird rausgerechnet. "
                f"**Cohen's d** und **p-Wert** beziehen sich auf den {_rs}-bereinigten Wert."
            )
        else:
            st.caption(
                f"Bewertest du Filme aus deinen prägenden Jahren ({fs['form_start']}–{fs['form_end']}) "
                f"besser als den Rest? Positiver Wert = Nostalgiker-Tendenz. "
                f"Ohne {_rs}-Daten kein Qualitätsabgleich möglich."
            )
        mc1, mc2, mc3, mc4 = st.columns(4)
        # Reihenfolge: roh zuerst (intuitiver), dann bereinigt, dann Statistik
        if fs.get('has_imdb') and fs.get('bias_raw') is not None:
            mc1.metric(
                'Bias (roh)', f"{fs['bias_raw']:+.2f}",
                help='Deine direkte Durchschnittsnote für Formativfilme minus den Rest. Intuitiv, aber von der Filmqualität beeinflusst.',
            )
            mc2.metric(
                f'Bias ({_rs}-bereinigt)', f"{fs['bias']:+.2f}",
                help=f'Gleiche Rechnung, aber jeder Film um seine {_rs}-Note korrigiert. Heraus kommt: liebst du diese Filme *über* das, was ihre Qualität erwarten würde?',
            )
        else:
            mc1.metric(
                'Bias', f"{fs['bias']:+.2f}",
                help='Positiv = du bewertest Formativfilme besser als den Rest.',
            )
            mc2.metric('Formativfilme', fs['n_form'])
        mc3.metric(
            "Cohen's d", f"{fs['cohens_d']:+.2f}",
            help=(
                'Effektgröße — wie stark ist der Unterschied wirklich? '
                '|d| < 0.5 = kleiner Effekt, 0.5–0.8 = mittlerer Effekt, > 0.8 = großer Effekt. '
                'Wichtiger als der p-Wert wenn nur wenige Formativfilme vorhanden sind.'
            ),
        )
        mc4.metric(
            'p-Wert', f"{fs['p_value']:.3f}",
            help=(
                f'Statistische Signifikanz des {_rs}-bereinigten Bias. '
                'p < 0.05 = Effekt ist mit >95% Wahrscheinlichkeit kein Zufall. '
                'Bei wenigen Formativfilmen (<50) ist p oft > 0.05, auch wenn ein echter Effekt vorliegt — '
                'dann zählt Cohen\'s d mehr.'
            ),
        )
        _effect_color = 'green' if abs(fs['cohens_d']) >= 0.5 else 'gray'
        _d_label = fs.get('effect_label', '')
        st.caption(
            f"n={fs['n_form']} Formativfilme | Ø formativ: {fs['form_avg']:.2f} | Ø Rest: {fs['nonform_avg']:.2f} | "
            f":{_effect_color}[Effekt {_d_label}] | "
            f":{'green' if fs['significant'] else 'gray'}[{sig_text}]"
        )
        if birth_year:
            _form_chart = '/tmp/formative_chart.png'
            save_formative_years_chart(df, birth_year, _form_chart)
            st.image(_form_chart, width='stretch')

    # Radar Chart
    st.divider()
    radar_path = '/tmp/radar_tmp.png'
    save_radar_chart(display_name, dims, radar_path)
    st.image(radar_path, width='stretch')

# ── LINKS: Persönlichkeitsprofil mit Dimension-Charts ────────────
with col_left:
    st.subheader('🧠 Persönlichkeitsprofil')
    dim_order = ['bewertungsstil', 'meinungsstaerke', 'geschmacksbreite', 'epoche', 'publikum']
    dim_labels = {
        'bewertungsstil':   'Bewertungsstil',
        'meinungsstaerke':  'Meinungsstärke',
        'geschmacksbreite': 'Geschmacksbreite',
        'epoche':           'Lieblingsepoche',
        'publikum':         'Publikumsgeschmack',
    }
    for key in dim_order:
        if key in dims:
            d = dims[key]
            st.markdown(f'**{d["emoji"]} {dim_labels[key]}** — {d["pole"]}')
            st.caption(d['desc'])
            _dim_path = f'/tmp/dim_{key}.png'
            save_single_dimension_chart(key, df, dims, _dim_path, rating_source=_rating_source)
            st.image(_dim_path, width='stretch')

# ── Genre-Tabelle ─────────────────────────────────────────────────
if 'genre_all' in topflop and not topflop['genre_all'].empty:
    st.divider()
    st.subheader('🎭 Genre-Analyse')
    bias = topflop.get('overall_bias', 0.0)
    st.caption(
        f'Gesamtbias: {bias:+.2f} | '
        f'**adj** = (eigene Ø − {_rating_source} Ø) − Gesamtbias — '
        f'positiv = magst du mehr als dein Durchschnitt erwarten lässt'
    )
    genre_df = topflop['genre_all'].copy()
    genre_df.index.name = 'Genre'
    genre_df = genre_df.rename(columns={
        'n': 'Filme', 'user_avg': 'Eigene Ø',
        'imdb_avg': f'{_rating_source} Ø', 'vs_imdb': 'vs. Schnitt', 'adj': 'Adj. ▲▼'
    })

    def color_adj(val):
        if isinstance(val, float):
            if val >= 0.2:  return 'color: #4caf50; font-weight: bold'
            if val <= -0.2: return 'color: #f44336; font-weight: bold'
        return ''

    st.dataframe(
        genre_df.style.map(color_adj, subset=['Adj. ▲▼']).format(precision=2), width='stretch',
    )

# ── Regisseure ────────────────────────────────────────────────────
# ── Regisseure ────────────────────────────────────────────────────
if 'dir_all' in topflop and not topflop['dir_all'].empty:
    st.divider()
    st.subheader('🎥 Regisseure')
    dcol1, dcol2 = st.columns(2)

    def _dir_films(director):
        if 'directors' not in df.columns:
            return pd.DataFrame()
        mask = df['directors'].str.contains(director, na=False, regex=False)
        cols_show = [c for c in ['title', 'year', 'user_rating', 'imdb_rating'] if c in df.columns]
        result = df[mask][cols_show].copy()
        result = result.rename(columns={
            'title': 'Titel', 'year': 'Jahr',
            'user_rating': 'Eigene', 'imdb_rating': _rating_source
        }).sort_values('Eigene', ascending=False)
        return result

    with dcol1:
        st.markdown('**Top 3**')
        for d, row in topflop['dir_top'].head(3).iterrows():
            with st.expander(f'{d}  —  Ø {row["user_avg"]:.1f}  ({int(row["n"])} Filme)'):
                _films = _dir_films(d)
                if not _films.empty:
                    st.dataframe(_films.style.format(precision=1), width='stretch', hide_index=True)
    with dcol2:
        st.markdown('**Flop 3**')
        for d, row in topflop['dir_flop'].head(3).iterrows():
            with st.expander(f'{d}  —  Ø {row["user_avg"]:.1f}  ({int(row["n"])} Filme)'):
                _films = _dir_films(d)
                if not _films.empty:
                    st.dataframe(_films.style.format(precision=1), width='stretch', hide_index=True)

# ── Größte Abweichungen ───────────────────────────────────────────
st.divider()
st.subheader('📐 Größte Abweichungen')

_has_imdb_dev = 'imdb_rating' in df.columns and df['imdb_rating'].notna().sum() >= 5
_has_david    = david_df is not None and not david_df.empty
_has_robert   = robert_df is not None and not robert_df.empty

if _has_imdb_dev or _has_david or _has_robert:
    _dev_cols = []
    if _has_imdb_dev: _dev_cols.append('vs. IMDB')
    if _has_david:    _dev_cols.append('vs. David')
    if _has_robert:   _dev_cols.append('vs. Robert')
    _dev_tab_labels = _dev_cols
    _dev_tabs = st.tabs(_dev_tab_labels)

    def _deviation_table(left_df, right_series, label, n=5):
        """Gibt top-n positive und negative Abweichungen zurück."""
        merged = left_df[['title', 'year', 'user_rating']].copy()
        merged = merged[merged['title'].notna()].copy()
        if hasattr(right_series, 'name'):
            merged['_other'] = merged['title'].map(
                right_series.reset_index().set_index(right_series.index.name or 'index')[right_series.name]
                if hasattr(right_series.index, 'name') else right_series
            )
        else:
            merged['_other'] = right_series.values if len(right_series) == len(merged) else float('nan')
        merged = merged.dropna(subset=['_other'])
        merged['diff'] = merged['user_rating'] - merged['_other']
        merged = merged.rename(columns={'title': 'Titel', 'year': 'Jahr',
                                        'user_rating': 'Eigene', '_other': label})
        merged['Diff'] = merged['diff']
        top    = merged.nlargest(n, 'diff')[['Titel', 'Jahr', 'Eigene', label, 'Diff']]
        bottom = merged.nsmallest(n, 'diff')[['Titel', 'Jahr', 'Eigene', label, 'Diff']]
        return top, bottom

    def _clean_dev_df(df_s):
        """Jahr als int, Ratings auf 1 Nachkommastelle."""
        df_s = df_s.copy()
        if 'Jahr' in df_s.columns:
            df_s['Jahr'] = pd.to_numeric(df_s['Jahr'], errors='coerce').astype('Int64')
        for col in ['Eigene', 'IMDB', 'David', 'Robert', 'Diff']:
            if col in df_s.columns:
                df_s[col] = pd.to_numeric(df_s[col], errors='coerce')
        return df_s

    def _style_diff(df_s, other_col=None):
        fmt = {'Eigene': '{:.1f}', 'Diff': '{:+.1f}'}
        if other_col and other_col in df_s.columns:
            fmt[other_col] = '{:.1f}'
        return df_s.style.map(
            lambda v: 'color: #4caf50; font-weight: bold' if isinstance(v, (int, float)) and v > 0
                 else ('color: #f44336; font-weight: bold' if isinstance(v, (int, float)) and v < 0 else ''),
            subset=['Diff']
        ).format(fmt, na_rep='—')

    _tab_idx = 0

    if _has_imdb_dev:
        with _dev_tabs[_tab_idx]:
            _tab_idx += 1
            _diff_series = df['imdb_rating'].copy()
            _diff_series.index = df.index
            _df_dev = df[['title', 'year', 'user_rating']].copy()
            _df_dev['_other'] = df['imdb_rating']
            _df_dev = _df_dev.dropna(subset=['_other'])
            _df_dev['diff'] = _df_dev['user_rating'] - _df_dev['_other']
            _top_imdb    = _df_dev.nlargest(5, 'diff').rename(
                columns={'title': 'Titel', 'year': 'Jahr', 'user_rating': 'Eigene', '_other': 'IMDB', 'diff': 'Diff'})
            _bottom_imdb = _df_dev.nsmallest(5, 'diff').rename(
                columns={'title': 'Titel', 'year': 'Jahr', 'user_rating': 'Eigene', '_other': 'IMDB', 'diff': 'Diff'})
            c1, c2 = st.columns(2)
            with c1:
                st.markdown('**⬆️ Du liebst, was andere nicht mögen**')
                st.dataframe(_style_diff(_clean_dev_df(_top_imdb[['Titel', 'Jahr', 'Eigene', 'IMDB', 'Diff']]), 'IMDB'), width='stretch', hide_index=True)
            with c2:
                st.markdown('**⬇️ Du magst nicht, was andere feiern**')
                st.dataframe(_style_diff(_clean_dev_df(_bottom_imdb[['Titel', 'Jahr', 'Eigene', 'IMDB', 'Diff']]), 'IMDB'), width='stretch', hide_index=True)

    def _vs_person(person_df, person_col, tab):
        """Abweichungen vs. David oder Robert (join über title_norm, Ratings auf 1–10)."""
        if person_df is None or person_df.empty:
            return
        # person_df hat title_norm + david_rating / robert_rating (1-10)
        _pcol_rating = 'david_rating' if 'david_rating' in person_df.columns else 'robert_rating'
        _pmap = person_df.set_index('title_norm')[_pcol_rating].to_dict()
        _merged = df[['title', 'year', 'user_rating', 'title_norm']].copy() \
                  if 'title_norm' in df.columns else df[['title', 'year', 'user_rating']].copy()
        if 'title_norm' not in _merged.columns:
            from film_personality import normalize_title as _nt
            _merged['title_norm'] = _merged['title'].apply(_nt)
        _merged['_prating'] = _merged['title_norm'].map(_pmap)
        _merged = _merged.dropna(subset=['_prating'])
        if _merged.empty:
            with tab:
                st.info(f'Keine gemeinsamen Filme mit {person_col} gefunden.')
            return
        _merged['diff'] = _merged['user_rating'] - _merged['_prating']
        _top    = _merged.nlargest(5, 'diff').rename(
            columns={'title': 'Titel', 'year': 'Jahr', 'user_rating': 'Eigene',
                     '_prating': person_col, 'diff': 'Diff'})
        _bottom = _merged.nsmallest(5, 'diff').rename(
            columns={'title': 'Titel', 'year': 'Jahr', 'user_rating': 'Eigene',
                     '_prating': person_col, 'diff': 'Diff'})
        with tab:
            st.caption(f'{len(_merged)} gemeinsame Filme gefunden.')
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f'**⬆️ Du magst deutlich mehr als {person_col}**')
                st.dataframe(_style_diff(_clean_dev_df(_top[['Titel', 'Jahr', 'Eigene', person_col, 'Diff']]), person_col), width='stretch', hide_index=True)
            with c2:
                st.markdown(f'**⬇️ Du magst deutlich weniger als {person_col}**')
                st.dataframe(_style_diff(_clean_dev_df(_bottom[['Titel', 'Jahr', 'Eigene', person_col, 'Diff']]), person_col), width='stretch', hide_index=True)

    if _has_david:
        _vs_person(david_df, 'David', _dev_tabs[_tab_idx])
        _tab_idx += 1
    if _has_robert:
        _vs_person(robert_df, 'Robert', _dev_tabs[_tab_idx])

else:
    st.info('Keine IMDB-Daten verfügbar für Abweichungsanalyse — TMDB-Key eingeben oder IMDB-Export hochladen.')

# ── Debug (versteckt, nur bei Bedarf aufklappen) ─────────────────
if _is_lb and api_key:
    with st.expander('🔧 Debug', expanded=False):
        for _line in _debug_api_lines:
            st.caption(_line)
        if '_enrich_error' in df_raw.columns:
            st.error(f'Enrichment-Fehler: {df_raw["_enrich_error"].iloc[0]}')
        _dcols = [c for c in ['title', 'year', 'tmdb_rating', 'genres', 'directors'] if c in df_raw.columns]
        _n_found = df_raw['tmdb_rating'].notna().sum() if 'tmdb_rating' in df_raw.columns else 0
        st.caption(f'TMDB: {_n_found}/{len(df_raw)} Filme gefunden · cache_path: {cache_path}')
        if 'tmdb_rating' in df_raw.columns:
            _missing = df_raw[df_raw['tmdb_rating'].isna()][['title', 'year']].copy()
            if not _missing.empty:
                st.markdown(f'**Nicht gefunden auf TMDB ({len(_missing)}):**')
                st.dataframe(_missing.reset_index(drop=True), width='stretch', hide_index=True)
        st.dataframe(df_raw[_dcols].head(), width='stretch', hide_index=True)

# ── Footer ────────────────────────────────────────────────────────
st.divider()
_footer_parts = [
    '🎙️ [Zwei wie Pech & Schwafel](https://open.spotify.com/show/22pGOX5N9KjeJajq1aH7Nt)',
    f'Daten: {"Letterboxd + TMDB" if _rating_source == "TMDB" else "IMDB"}',
    'Ratings werden nur bei Opt-in gespeichert.',
]
if _fb.is_available():
    try:
        _footer_stats = _fb.get_community_stats()
        if _footer_stats and _footer_stats.get('total_users', 0) > 0:
            _footer_pool_films = _footer_stats.get('total_films') or _footer_stats.get('avg_films', '')
            _footer_parts.append(
                f"🤝 Filmbuddy-Pool: {_footer_stats['total_users']} Nutzer · {_footer_pool_films} Filme"
            )
    except Exception:
        pass
st.caption(' • '.join(_footer_parts))
