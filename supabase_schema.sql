-- ─────────────────────────────────────────────────────────────────
-- ZWPUS Filmbuddy — Supabase Schema
-- Im Supabase SQL Editor ausführen (einmalig)
-- ─────────────────────────────────────────────────────────────────

-- Nutzer (display_name ist eindeutig — dient als Login-Ersatz)
CREATE TABLE IF NOT EXISTS fb_users (
  id            UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name  TEXT    UNIQUE NOT NULL,
  film_count    INTEGER DEFAULT 0,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  last_upload   TIMESTAMPTZ DEFAULT NOW()
);

-- Ratings: title_norm + year (0 wenn unbekannt) als Identifier
CREATE TABLE IF NOT EXISTS fb_ratings (
  user_id     UUID    NOT NULL REFERENCES fb_users(id) ON DELETE CASCADE,
  title_norm  TEXT    NOT NULL,
  year        SMALLINT NOT NULL DEFAULT 0,
  user_rating NUMERIC(4,2) NOT NULL,
  PRIMARY KEY (user_id, title_norm, year)
);

-- Achievements
CREATE TABLE IF NOT EXISTS fb_achievements (
  user_id  UUID NOT NULL REFERENCES fb_users(id) ON DELETE CASCADE,
  key      TEXT NOT NULL,
  name     TEXT NOT NULL,
  emoji    TEXT DEFAULT '',
  PRIMARY KEY (user_id, key)
);

-- Row Level Security: lesen + schreiben für alle (kein Login nötig)
ALTER TABLE fb_users        ENABLE ROW LEVEL SECURITY;
ALTER TABLE fb_ratings      ENABLE ROW LEVEL SECURITY;
ALTER TABLE fb_achievements ENABLE ROW LEVEL SECURITY;

CREATE POLICY "public_all_users"   ON fb_users        FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "public_all_ratings" ON fb_ratings       FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "public_all_ach"     ON fb_achievements  FOR ALL USING (true) WITH CHECK (true);

-- Index für schnelle User-ID-Lookups in Ratings
CREATE INDEX IF NOT EXISTS idx_ratings_user ON fb_ratings(user_id);
