# Screenplay Production Analysis — local app

## Setup (one time)
```
cd screenplay_app
pip install -r requirements.txt
```

## Run order

**1. Import your .fdx file**
```
python import_fdx.py "When_Strangers_meet.fdx" --title "When Strangers Meet"
```
Prints a `script_id` (e.g. `1`) and copies the file into `data/imports/`.

**2. Parse it into the database**
```
python parse_and_store.py --script-id 1
```
Populates `scene`, `character`, and `scene_character` in `data/production.db`.
Safe to re-run after re-importing a revised draft — it wipes and rebuilds
that script's scenes each time.

**3. Start the API (leave running)**
```
python api.py
```
Serves REST endpoints at http://127.0.0.1:8000 (interactive docs at
http://127.0.0.1:8000/docs).

**4. Start the interface (in a second terminal, leave running)**
```
python interface.py
```
Opens http://127.0.0.1:3000 in your browser — a dashboard listing every
scene (INT/EXT, location, time of day, page length, characters), and a
Characters tab. Click a scene to expand it: full scene text, plus add/remove
controls for props, vehicles, and costumes (typed in directly — nothing is
auto-extracted for those three).

## Notes
- Characters tagged with a `*` were found only by scanning action text for
  their name (silent/background appearance), not from a Character cue —
  worth a glance since it's a text match, not a guarantee.
- Locations get their own table now, deduplicated case-insensitively (and
  curly vs. straight apostrophes are unified) so "COACHING CLASSROOM" and
  "Coaching classroom" become one row instead of two. Genuinely different
  text (e.g. a missing word) won't auto-merge — rename one to match the
  other from a scene's expanded view in the UI if they should be the same
  place; renaming to an existing name (any casing) merges into it.
- `models.py` is shared table-definition code used by `parse_and_store.py`
  and `api.py`; it isn't one of the four scripts but has to live somewhere
  so the schema isn't duplicated.
- The database file lives at `data/production.db` — **delete it and re-run
  steps 1–2 if you're upgrading from an earlier version of this app**, since
  SQLite won't automatically migrate `scene.location` (a text column) into
  the new `location` table + `scene.location_id` foreign key.
