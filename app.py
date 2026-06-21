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
    save_radar_chart, save_single_dimension_chart,
    save_formative_years_chart, compute_formative_years_stats,
)


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
            import json
            # Schon warm genug?
            try:
                with open(cache_path) as _f:
                    _data = json.load(_f)
                if len(_data) > 1000:
                    return
            except Exception:
                pass

            from tmdb_enrich import enrich_letterboxd

            for fname in ['david_ratings.csv', 'robert_ratings.csv']:
                fpath = os.path.join(script_dir, fname)
                if not os.path.exists(fpath):
                    continue
                df_w = pd.read_csv(fpath)
                if 'rating' in df_w.columns:
                    df_w['user_rating'] = df_w['rating'] * 2
                if 'title' not in df_w.columns and len(df_w.columns) > 1:
                    df_w = df_w.rename(columns={df_w.columns[1]: 'title'})
                enrich_letterboxd(df_w, api_key, cache_path=cache_path,
                                  progress_cb=None)
        except Exception:
            pass   # Warming-Fehler sind nicht kritisch

    _threading.Thread(target=_warm, daemon=True).start()


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

    # Cache-Status
    _cpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmdb_cache.json')
    try:
        import json as _json
        _n = len(_json.load(open(_cpath)))
        st.caption(f'📦 TMDB-Cache: {_n} Filme gecacht')
    except Exception:
        if _cache_warmed:
            st.caption('⏳ Cache wird vorbereitet...')

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
_script_dir_early = os.path.dirname(os.path.abspath(__file__))
_start_cache_warming(api_key, _script_dir_early, cache_path)

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
    if done == total:
        _prog_bar.empty()
        _prog_text.empty()

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


# ── Profil berechnen ──────────────────────────────────────────────
with st.spinner('Berechne Profil...'):
    dims        = compute_dimensions(df)
    bonus       = compute_bonus_achievements(df, birth_year, david_df, robert_df)
    genre_ach   = compute_genre_achievements(df)
    insider     = compute_insider_achievements(df, df_raw)
    progressive = compute_progressive_achievements(df_raw)
    topflop     = compute_top_flop(df)

display_name = name.strip() if name.strip() else 'Anonym'

# Formative-Jahre-Bias + Signifikanz
formative_stats = compute_formative_years_stats(df, birth_year)

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
    if formative_stats is not None:
        fs = formative_stats
        st.divider()
        bias_label = 'Nostalgiker 💝' if fs['bias'] > 0 else 'Antichrist 😈'
        sig_text = '(statistisch signifikant ✓)' if fs['significant'] else '(nicht signifikant)'
        sig_color = '#4caf50' if fs['significant'] else '#888888'
        method_note = '(IMDB-bereinigt)' if fs.get('has_imdb') else '(Rohrating)'
        st.markdown(f"**🎞️ Prägende Jahre {fs['form_start']}–{fs['form_end']}** {method_note}")
        st.caption(
            f"Positive Zahl = du bewertest Filme aus deinen prägenden Jahren "
            f"**besser** als den Rest deiner Sammlung. {method_note}: Bias wird relativ "
            f"zum IMDB-Schnitt gemessen, um Ären-Unterschiede herauszurechnen."
        )
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric('Bias', f"{fs['bias']:+.2f}", help='Formativfilm-Ø minus Rest-Ø. Positiv = Nostalgiker.')
        mc2.metric('Formative Filme', fs['n_form'])
        mc3.metric('p-Wert', f"{fs['p_value']:.3f}")
        st.caption(
            f"Ø formative Filme: {fs['form_avg']:.2f} | Ø restliche Filme: {fs['nonform_avg']:.2f} | "
            f"t={fs['t_stat']:.2f} | "
            f":{'green' if fs['significant'] else 'gray'}[{sig_text}] — "
            f"{'Der Bias ist statistisch belastbar.' if fs['significant'] else 'Zu wenig Daten oder Effekt zu klein.'}"
        )
        # Formative-Jahre-Chart
        if birth_year:
            _form_chart = '/tmp/formative_chart.png'
            save_formative_years_chart(df, birth_year, _form_chart)
            st.image(_form_chart, use_container_width=True)

    # Dimensionen mit je eigenem Chart
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
            _dim_path = f'/tmp/dim_{key}.png'
            save_single_dimension_chart(key, df, dims, _dim_path)
            st.image(_dim_path, use_container_width=True)

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
