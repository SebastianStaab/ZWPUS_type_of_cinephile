# 🎬 Zwei wie Pech & Schwafel — Film Personality Test

Ein Persönlichkeitstest für die ZWPUS-Community, basierend auf deinen Letterboxd- oder IMDB-Ratings.

**👉 App starten:** [zweiwiepechundschwafel.streamlit.app](https://zweiwiepechundschwafel.streamlit.app)

---

## Was macht die App?

Du lädst deinen Ratings-Export hoch und bekommst:

- **5 Persönlichkeitsdimensionen** mit eigenen Detail-Charts — Bewertungsstil, Meinungsstärke, Geschmacksbreite, Lieblingsepoche, Publikumsgeschmack (Arthouse ↔ Blockbuster)
- **Radar-Chart** deines Filmprofils
- **Prägenden Jahre Bias** — bewertest du Filme aus deiner Jugend anders? Inkl. p-Wert und statistischer Signifikanz
- **Achievements** — von „Cinephile" über „Hidden Gem Hunter" bis „Tony Surroundi" (nur für echte ZWPUS-Fans 👀)
- **Genre-Analyse** mit adjustiertem Score — inklusive positiver (Lieblingsgenres) und negativer (Hass-Genres) Achievements
- **Top 3 / Flop 3 Regisseure**
- **Team David oder Team Robert?**

---

## Export herunterladen

Alle Ratings werden intern auf die **1–10 Skala** normalisiert, egal ob du Letterboxd (0,5–5 Sterne) oder IMDB verwendest. Das stellt sicher, dass Vergleiche zwischen Nutzern und mit David/Roberts Ratings fair sind.

### Letterboxd
1. letterboxd.com → Profil → Einstellungen → **Daten** (ganz unten)
2. „Export Your Data" → ZIP herunterladen
3. `ratings.csv` aus dem ZIP in die App hochladen

> ⏳ **Hinweis:** Bei Letterboxd-Exporten wird jeder Film über die TMDB-API mit Genres, Regisseur und Publikumsgröße angereichert. Das kann bei großen Sammlungen **mehrere Minuten** dauern — einfach den Spinner laufen lassen. Bereits abgefragte Filme werden gecacht, wiederholte Uploads sind deutlich schneller.

### IMDB
1. imdb.com → Dein Profil (oben rechts) → **Your ratings**
2. Drei Punkte (`...`) → **Export**
3. CSV direkt hochladen — Genres & Regisseure sind bereits enthalten, geht sofort

---

## Technisches

**Stack:** Python, Streamlit, pandas, numpy, scipy, matplotlib, TMDB API

**Hosting:** Streamlit Community Cloud (kostenlos)

**Datenschutz:** Keine Ratings werden gespeichert. Alles läuft nur im RAM für die Dauer deiner Session.

**TMDB Cache:** Die App cached TMDB-Lookups serverseitig (`tmdb_cache.json`). Der Cache überlebt zwischen Deployments (solange der Streamlit-Worker nicht neu gestartet wird) — neue Pushes räumen ihn nicht ab, da die Datei gitignored ist. Beim ersten Start nach einem Worker-Neustart werden Davids und Roberts Ratings automatisch im Hintergrund vorgeladen.

**Ratings-Quelle:** Bei Letterboxd-Exporten stammen Crowd-Ratings und Vote-Counts von TMDB. Bei IMDB-Exporten direkt von IMDB. Die Arthouse/Blockbuster-Dimension passt die Schwellenwerte automatisch an die jeweilige Quelle an (TMDB-Vote-Counts sind ~50–100x kleiner als IMDB).

---

## Datenquellen

- **David Hain** (`david_ratings.csv`): Letterboxd-Profil [@behaind](https://letterboxd.com/behaind/) — vollständig gescrapt (~3.500+ Ratings)
- **Robert Hoffmann** (`robert_ratings.csv`): Zusammengeführt aus [@robsntown](https://letterboxd.com/robsntown/) und [@roberthofmannio](https://letterboxd.com/roberthofmannio/) (~3.000 Ratings). Bei Überschneidungen hat robsntown Priorität.

Die Daten wurden mit einem Chrome-Extension-basierten Scraper erhoben (Browser-Cookies nötig, da Letterboxd API-Zugriff blockiert).

---

## Lokal ausführen

```bash
git clone https://github.com/Seys97/ZWPUS_type_of_cinephile
cd ZWPUS_type_of_cinephile
pip install -r requirements.txt

# TMDB-Key in .streamlit/secrets.toml eintragen:
# TMDB_API_KEY = "dein-key"

streamlit run app.py
```

---

## Rechtliches

This product uses the TMDB API but is not endorsed or certified by TMDB.

<img src="https://www.themoviedb.org/assets/2/v4/logos/v2/blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg" width="150" alt="TMDB Logo">

---

*Made with ❤️ für die ZWPUS-Community*
