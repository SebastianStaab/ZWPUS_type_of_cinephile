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
    save_radar_chart, save_dimension_bars_chart,
)

# ── Seitenkonfiguration ───────────────────────────────────────────
st.set_page_config(
    page_title='ZWPUS Film Personality',
    page_icon='🎬',
    layout='wide',
)

# ── TMDB-Key aus Streamlit-Secrets oder Eingabe ───────────────────
def get_api_key():
    try:
        return st.secrets['TMDB_API_KEY']
    except Exception:
        return st.session_state.get('tmdb_key', '')

# ── Sidebar ───────────────────────────────────────────────────────
with st.sidebar:
    st.image('https://img.shields.io/badge/Zwei%20wie%20Pech%20%26%20Schwafel-%F0%9F%8E%AC-red',
             use_container_width=True)
    st.title('🎬 Film Personality')
    st.caption('Powered by Letterboxd + TMDB')
    st.divider()

    name       = st.text_input('Dein Name', value='')
    birth_year = st.number_input('Geburtsjahr (optional)', min_value=1920,
                                 max_value=2010, value=1995, step=1)
    birth_year = int(birth_year) if birth_year else None

    try:
        _key_from_secrets = st.secrets['TMDB_API_KEY']
        st.session_state['tmdb_key'] = _key_from_secrets
        st.caption('✅ TMDB API-Key konfiguriert')
    except Exception:
        api_key_input = st.text_input('TMDB API-Key', type='password',
                                       help='Kostenlos auf themoviedb.org registrieren')
        if api_key_input:
            st.session_state['tmdb_key'] = api_key_input

    st.divider()
    st.markdown(
        '**Letterboxd-Export:**\n'
        'letterboxd.com → Profil → Einstellungen → Daten → Export\n\n'
        '**IMDB-Export** wird ebenfalls unterstützt.'
    )

# ── Hauptbereich ──────────────────────────────────────────────────
st.title('🎬 Zwei wie Pech & Schwafel')
st.subheader('Dein Film-Persönlichkeitstest')

uploaded = st.file_uploader(
    'Ratings-CSV hochladen (Letterboxd oder IMDB Export)',
    type=['csv'],
    help='Letterboxd: Einstellungen → Daten → Export | IMDB: imdb.com → Deine Ratings → Export'
)

if not uploaded:
    st.info('⬆️ Lade deine Ratings-CSV hoch um loszulegen.')
    with st.expander('Wie funktioniert das?'):
        st.markdown(
            '1. **Letterboxd-Export**: letterboxd.com → Profil → Einstellungen → Daten → "Export Your Data"\n'
            '2. **IMDB-Export**: imdb.com → Deine Ratings → ... → CSV exportieren\n'
            '3. CSV hier hochladen, Namen eingeben, fertig!\n\n'
            'Mit einem TMDB-API-Key (kostenlos) werden Genres und Regisseure automatisch ergänzt.'
        )
    st.stop()

# ── Daten laden ───────────────────────────────────────────────────
api_key    = st.session_state.get('tmdb_key', '')
cache_path = os.path.join(os.path.dirname(__file__), 'tmdb_cache.json')

with st.spinner('Lade Ratings...'):
    try:
        # Datei in temporären Pfad schreiben
        tmp_path = '/tmp/ratings_upload.csv'
        with open(tmp_path, 'wb') as f:
            f.write(uploaded.read())

        df, df_raw = detect_and_load(
            tmp_path,
            api_key=api_key if api_key else None,
            cache_path=cache_path,
        )
    except Exception as e:
        st.error(f'Fehler beim Laden: {e}')
        st.stop()

script_dir = os.path.dirname(os.path.abspath(__file__))
david_df, robert_df = load_david_robert(script_dir)

# ── Profil berechnen ──────────────────────────────────────────────
with st.spinner('Berechne Profil...'):
    dims        = compute_dimensions(df)
    bonus       = compute_bonus_achievements(df, birth_year, david_df, robert_df)
    genre_ach   = compute_genre_achievements(df)
    insider     = compute_insider_achievements(df, df_raw)
    progressive = compute_progressive_achievements(df_raw)
    topflop     = compute_top_flop(df)

display_name = name.strip() if name.strip() else 'Anonym'

# Formative-Jahre-Bias berechnen
formative_bias = None
formative_n = 0
if birth_year and 'year' in df.columns:
    _form = df[df['year'].between(birth_year, birth_year + 19)]
    _nonform = df[~df['year'].between(birth_year, birth_year + 19)]
    if len(_form) >= 5 and len(_nonform) >= 5:
        formative_bias = float(_form['user_rating'].mean() - _nonform['user_rating'].mean())
        formative_n = len(_form)

# ── Layout ────────────────────────────────────────────────────────
col_left, col_right = st.columns([1, 1], gap='large')

with col_left:
    # Basisdaten
    st.metric('Filme bewertet', len(df))
    m1, m2, m3 = st.columns(3)
    m1.metric('Eigene Ø', f'{df["user_rating"].mean():.2f}')
    if df['imdb_rating'].notna().sum() > 10:
        bias = (df['user_rating'] - df['imdb_rating']).mean()
        m2.metric('IMDB Ø', f'{df["imdb_rating"].mean():.2f}')
        m3.metric('Bias', f'{bias:+.2f}')

    # Formative-Jahre-Bias anzeigen
    if formative_bias is not None:
        st.divider()
        bias_label = 'Nostalgiker' if formative_bias > 0 else 'Anti-Nostalgiker'
        st.metric(
            f'🎞️ Formative Jahrezahl Bias ({birth_year}\u2013{birth_year+19})',
            f'{formative_bias:+.2f}',
            help=f'Formativfilm-Ratings vs. Rest. n={formative_n} Filme. Positiv = {bias_label}'
        )

    # Dimensionen
    st.divider()
    st.subheader('🧠 Persönlichkeitsprofil')
    dim_order = ['bewertungsstil', 'meinungsstaerke', 'geschmacksbreite', 'epoche']
    dim_labels = {
        'bewertungsstil':   'Bewertungsstil',
        'meinungsstaerke':  'Meinungsstärke',
        'geschmacksbreite': 'Geschmacksbreite',
        'epoche':           'Lieblingsepoche',
    }
    for key in dim_order:
        if key in dims:
            d = dims[key]
            st.markdown(f'**{d["emoji"]} {dim_labels[key]}** — {d["pole"]}')
            st.caption(d['desc'])

    # Dimension Bars Chart
    bars_path = '/tmp/dim_bars_tmp.png'
    save_dimension_bars_chart(display_name, dims, bars_path)
    st.image(bars_path, use_container_width=True)

with col_right:
    # Radar Chart
    radar_path = '/tmp/radar_tmp.png'
    save_radar_chart(display_name, dims, radar_path)
    st.image(radar_path, use_container_width=True)

# ── Achievements ──────────────────────────────────────────────────
all_ach = progressive + bonus + genre_ach + insider
if all_ach:
    st.divider()
    st.subheader(f'🏅 Achievements ({len(all_ach)})')
    cols = st.columns(min(len(all_ach), 3))
    for i, a in enumerate(all_ach):
        with cols[i % 3]:
            st.markdown(
                f'<div style="border:1px solid #333;border-radius:8px;padding:12px;margin:4px">'
                f'<div style="font-size:2em">{a["emoji"]}</div>'
                f'<b>{a["name"]}</b><br>'
                f'<small>{a["desc"]}</small>'
                f'</div>',
                unsafe_allow_html=True
            )

# ── Genre-Tabelle ─────────────────────────────────────────────────
if 'genre_all' in topflop and not topflop['genre_all'].empty:
    st.divider()
    st.subheader('🎭 Genre-Analyse')
    bias = topflop.get('overall_bias', 0.0)
    st.caption(
        f'Gesamtbias: {bias:+.2f} | '
        f'**adj** = (eigene Ø − IMDB Ø) − Gesamtbias — '
        f'positiv = magst du mehr als dein Durchschnitt erwarten lässt'
    )
    genre_df = topflop['genre_all'].copy()
    genre_df.index.name = 'Genre'
    genre_df = genre_df.rename(columns={
        'n': 'Filme', 'user_avg': 'Eigene Ø',
        'imdb_avg': 'TMDB/IMDB Ø', 'vs_imdb': 'vs. Schnitt', 'adj': 'Adj. ▲▼'
    })

    def color_adj(val):
        if isinstance(val, float):
            if val >= 0.2:  return 'color: #4caf50; font-weight: bold'
            if val <= -0.2: return 'color: #f44336; font-weight: bold'
        return ''

    st.dataframe(
        genre_df.style.map(color_adj, subset=['Adj. ▲▼']).format(precision=2),
        use_container_width=True,
    )

# ── Regisseure ────────────────────────────────────────────────────
if 'dir_all' in topflop and not topflop['dir_all'].empty:
    st.divider()
    st.subheader('🎥 Regisseure')
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        st.markdown('**Top 3**')
        for d, row in topflop['dir_top'].head(3).iterrows():
            st.markdown(f'`{d}` — Ø {row["user_avg"]:.1f} (n={int(row["n"])})')
    with dcol2:
        st.markdown('**Flop 3**')
        for d, row in topflop['dir_flop'].head(3).iterrows():
            st.markdown(f'`{d}` — Ø {row["user_avg"]:.1f} (n={int(row["n"])})')

# ── Footer ────────────────────────────────────────────────────────
st.divider()
st.caption(
    '🎙️ [Zwei wie Pech & Schwafel](https://www.imdb.com/title/tt...) • '
    'Daten: Letterboxd + TMDB • '
    'Ratings werden nicht gespeichert.'
)
