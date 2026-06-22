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
        'letterboxd.com → Profil → Einstellungen → **Daten** → Export Your Data\n'
        '→ ZIP öffnen → `ratings.csv` hochladen\n\n'
        '**IMDB-Export:**\n'
        'imdb.com → Profil → Your ratings → `...` → Export\n'
        '→ CSV direkt hochladen (sofort, kein TMDB nötig)'
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
        m2.metric('IMDB Ø', f'{df["imdb_rating"].mean():.2f}')
        m3.metric('Bias', f'{_bias_val:+.2f}')

    # Prägende Jahre
    if formative_stats is not None:
        fs = formative_stats
        st.divider()
        sig_text = '(signifikant ✓)' if fs['significant'] else '(nicht signifikant)'
        method_note = 'IMDB-bereinigt' if fs.get('has_imdb') else 'Rohrating'
        st.markdown(f"**🎞️ Prägende Jahre {fs['form_start']}–{fs['form_end']}** _{method_note}_")
        st.caption(
            f"Positive Zahl = du bewertest Filme aus deinen prägenden Jahren **besser** als den Rest. "
            f"Bias wird relativ zum IMDB-Schnitt gemessen, um Ären-Unterschiede herauszurechnen."
        )
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric(
            'Bias (IMDB-rel.)' if fs.get('has_imdb') else 'Bias (roh)',
            f"{fs['bias']:+.2f}",
            help='Positiv = Nostalgiker. IMDB-bereinigt = Ären-Unterschiede herausgerechnet.',
        )
        if fs.get('has_imdb') and fs.get('bias_raw') is not None:
            mc2.metric(
                'Bias (roh)', f"{fs['bias_raw']:+.2f}",
                help='Wie viel besser du Filme aus deinen prägenden Jahren bewertest — ohne IMDB-Korrektur.',
            )
        else:
            mc2.metric('Prägende Filme', fs['n_form'])
        mc3.metric(
            "Cohen's d", f"{fs['cohens_d']:+.2f}",
            help='Effektgröße: |d|<0.5 = klein, 0.5–0.8 = mittel, >0.8 = groß. Aussagekräftiger als p-Wert bei kleinen Stichproben.',
        )
        mc4.metric('p-Wert', f"{fs['p_value']:.3f}")
        _effect_color = 'green' if abs(fs['cohens_d']) >= 0.5 else 'gray'
        _d_label = fs.get('effect_label', '')
        st.caption(
            f"n={fs['n_form']} Formativfilme | Ø formativ: {fs['form_avg']:.2f} | Ø Rest: {fs['nonform_avg']:.2f} | "
            f"t={fs['t_stat']:.2f} | :{_effect_color}[Effekt {_d_label}] | "
            f":{'green' if fs['significant'] else 'gray'}[{sig_text}]"
        )
        if birth_year:
            _form_chart = '/tmp/formative_chart.png'
            save_formative_years_chart(df, birth_year, _form_chart)
            st.image(_form_chart, use_container_width=True)

    # Radar Chart
    st.divider()
    radar_path = '/tmp/radar_tmp.png'
    save_radar_chart(display_name, dims, radar_path)
    st.image(radar_path, use_container_width=True)

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
            save_single_dimension_chart(key, df, dims, _dim_path)
            st.image(_dim_path, use_container_width=True)

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
            'user_rating': 'Eigene', 'imdb_rating': 'IMDB'
        }).sort_values('Eigene', ascending=False)
        return result

    with dcol1:
        st.markdown('**Top 3**')
        for d, row in topflop['dir_top'].head(3).iterrows():
            with st.expander(f'{d}  —  Ø {row["user_avg"]:.1f}  ({int(row["n"])} Filme)'):
                _films = _dir_films(d)
                if not _films.empty:
                    st.dataframe(_films.style.format(precision=1), use_container_width=True, hide_index=True)
    with dcol2:
        st.markdown('**Flop 3**')
        for d, row in topflop['dir_flop'].head(3).iterrows():
            with st.expander(f'{d}  —  Ø {row["user_avg"]:.1f}  ({int(row["n"])} Filme)'):
                _films = _dir_films(d)
                if not _films.empty:
                    st.dataframe(_films.style.format(precision=1), use_container_width=True, hide_index=True)

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

    def _style_diff(df_s):
        return df_s.style.map(
            lambda v: 'color: #4caf50; font-weight: bold' if isinstance(v, float) and v > 0
                 else ('color: #f44336; font-weight: bold' if isinstance(v, float) and v < 0 else ''),
            subset=['Diff']
        ).format({'Eigene': '{:.1f}', 'Diff': '{:+.1f}'}, na_rep='—')

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
                st.dataframe(_style_diff(_top_imdb[['Titel', 'Jahr', 'Eigene', 'IMDB', 'Diff']]).format(
                    {'IMDB': '{:.1f}'}), use_container_width=True, hide_index=True)
            with c2:
                st.markdown('**⬇️ Du magst nicht, was andere feiern**')
                st.dataframe(_style_diff(_bottom_imdb[['Titel', 'Jahr', 'Eigene', 'IMDB', 'Diff']]).format(
                    {'IMDB': '{:.1f}'}), use_container_width=True, hide_index=True)

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
                st.dataframe(_style_diff(_top[['Titel', 'Jahr', 'Eigene', person_col, 'Diff']]).format(
                    {person_col: '{:.1f}'}), use_container_width=True, hide_index=True)
            with c2:
                st.markdown(f'**⬇️ Du magst deutlich weniger als {person_col}**')
                st.dataframe(_style_diff(_bottom[['Titel', 'Jahr', 'Eigene', person_col, 'Diff']]).format(
                    {person_col: '{:.1f}'}), use_container_width=True, hide_index=True)

    if _has_david:
        _vs_person(david_df, 'David', _dev_tabs[_tab_idx])
        _tab_idx += 1
    if _has_robert:
        _vs_person(robert_df, 'Robert', _dev_tabs[_tab_idx])

else:
    st.info('Keine IMDB-Daten verfügbar für Abweichungsanalyse — TMDB-Key eingeben oder IMDB-Export hochladen.')

# ── Footer ────────────────────────────────────────────────────────
st.divider()
st.caption(
    '🎙️ [Zwei wie Pech & Schwafel](https://www.imdb.com/title/tt...) • '
    'Daten: Letterboxd + TMDB • '
    'Ratings werden nicht gespeichert.'
)
