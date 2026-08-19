"""
parse_and_store.py
-------------------
SCRIPT 2 of 4.

Parses a Final Draft (.fdx) file that was previously registered by
import_fdx.py, and populates the `scene`, `character`, and `scene_character`
tables.

Two-pass approach (as discussed):
  Pass 1 — walk every "Character" paragraph in the whole document to build
           the canonical character roster (handles inconsistent casing like
           "RAJESH" / "Rajesh" / "RaJESH" all referring to one person).
  Pass 2 — walk the document again scene by scene. Within each scene:
           - characters found via Character-cue paragraphs are linked with
             is_auto_detected = False (high confidence: they speak here)
           - the scene's Action text is then scanned for mentions of any
             OTHER roster name (silent/background appearances) and linked
             with is_auto_detected = True (lower confidence: text match only)

Notes specific to this FDX format (confirmed by inspection):
  - A <Paragraph> can contain multiple <Text> runs (e.g. special-character
    fonts splitting a sentence). All runs are concatenated in order.
  - <SceneProperties Length="..." Page="..."> on Scene Heading paragraphs
    gives page length (in eighths, e.g. "1 7/8") and starting page directly
    — no need to compute this ourselves.
  - Scene heading casing is inconsistent ("Int.", "INt.", "EXT.") so
    INT/EXT parsing is case-insensitive.

Props / vehicles / costumes are intentionally NOT extracted here — per your
instruction, those are added manually through the UI (see api.py /
interface.py), not inferred from text.

Usage:
    python parse_and_store.py --script-id 1
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from models import Scene, Character, SceneCharacter, Location, Script, SessionLocal, init_db

BODY_TYPES = {"Action", "Character", "Dialogue", "Parenthetical"}


# ---------------------------------------------------------------------------
# Low-level FDX helpers
# ---------------------------------------------------------------------------

def paragraph_text(paragraph: ET.Element) -> str:
    """Concatenate all <Text> runs within a <Paragraph>, in document order."""
    return "".join(t.text or "" for t in paragraph.findall("Text"))


def normalize_name(raw: str) -> str:
    """Collapse whitespace and uppercase, used as the dedup key for names."""
    return re.sub(r"\s+", " ", raw.strip()).upper()


def normalize_location(raw: str) -> str:
    """Dedup key for location names. Beyond whitespace/case, this also
    unifies curly and straight apostrophes ("Rajesh's" vs "Rajesh's") —
    confirmed present in real scene headings, and plain .upper() alone
    does not merge them since they're different Unicode characters."""
    text = (raw or "").strip()
    text = text.replace("\u2019", "'").replace("\u2018", "'")  # ' ' -> '
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(".")
    return text.upper()


def parse_length_to_eighths(raw: str | None) -> float | None:
    """Convert Final Draft's Length string (e.g. '1 7/8', '5/8', '2') to a
    decimal number of pages, e.g. '1 7/8' -> 1.875."""
    if not raw or not raw.strip():
        return None
    total = 0.0
    for part in raw.strip().split():
        if "/" in part:
            num, den = part.split("/")
            total += float(num) / float(den)
        else:
            total += float(part)
    return round(total, 3)


SLUGLINE_RE = re.compile(
    r"^\s*(INT\.?/EXT\.?|EXT\.?/INT\.?|I/E\.?|INT\.?|EXT\.?)\s*[.:]?\s*(.*)$",
    re.IGNORECASE,
)


def parse_slugline(heading_text: str):
    """Split a scene heading like 'Int. Coaching classroom - day' into
    (ext_int, location, time_of_day). Falls back gracefully on anything
    that doesn't match the standard pattern."""
    text = heading_text.strip()
    m = SLUGLINE_RE.match(text)
    if not m:
        return "INT", text, ""

    prefix, rest = m.groups()
    prefix_norm = prefix.upper().replace(".", "")
    if "/" in prefix_norm:
        ext_int = "INT/EXT"
    elif prefix_norm.startswith("INT"):
        ext_int = "INT"
    else:
        ext_int = "EXT"

    rest = rest.strip()
    if "-" in rest:
        location, time_of_day = rest.rsplit("-", 1)
        location = location.strip(" -")
        time_of_day = time_of_day.strip()
    else:
        location, time_of_day = rest, ""

    return ext_int, location, time_of_day.upper()


# ---------------------------------------------------------------------------
# Parsing data structures
# ---------------------------------------------------------------------------

@dataclass
class ScenePayload:
    order_index: int
    ext_int: str
    location: str
    time_of_day: str
    page_length: float | None
    page_number: int | None
    location_key: str = ""
    raw_text_parts: list = field(default_factory=list)
    confirmed_characters: set = field(default_factory=set)     # via Character cue (speaks)
    auto_detected_characters: set = field(default_factory=set)  # via Action-text mention only

    @property
    def raw_text(self) -> str:
        return "\n".join(p for p in self.raw_text_parts if p.strip())


# ---------------------------------------------------------------------------
# Pass 1: build the canonical character roster from the whole document
# ---------------------------------------------------------------------------

def build_roster(root: ET.Element) -> dict:
    """Returns {normalized_name: display_name}. Display name is chosen as
    the most frequently occurring casing variant for that character."""
    variant_counts: dict[str, dict[str, int]] = {}

    for paragraph in root.iter("Paragraph"):
        if paragraph.get("Type") != "Character":
            continue
        text = paragraph_text(paragraph).strip()
        if not text:
            continue
        key = normalize_name(text)
        variant_counts.setdefault(key, {})
        variant_counts[key][text] = variant_counts[key].get(text, 0) + 1

    roster = {}
    for key, variants in variant_counts.items():
        display = max(variants.items(), key=lambda kv: kv[1])[0]
        # Prefer a clean Title Case rendering for readability, e.g. "RAJESH" -> "Rajesh"
        roster[key] = display.title() if display.isupper() else display
    return roster


def build_location_roster(root: ET.Element) -> dict:
    """Same idea as build_roster, but for scene locations. Returns
    {normalized_location_key: display_name}. Scans every Scene Heading up
    front so all scenes resolve against one consistent roster, the same way
    character names do."""
    content = root.find("Content")
    if content is None:
        return {}

    variant_counts: dict[str, dict[str, int]] = {}
    for paragraph in content.findall("Paragraph"):
        if paragraph.get("Type") != "Scene Heading":
            continue
        text = paragraph_text(paragraph)
        _, location, _ = parse_slugline(text)
        location = location.strip() or "Unknown"
        key = normalize_location(location)
        variant_counts.setdefault(key, {})
        variant_counts[key][location] = variant_counts[key].get(location, 0) + 1

    roster = {}
    for key, variants in variant_counts.items():
        display = max(variants.items(), key=lambda kv: kv[1])[0]
        roster[key] = display.title() if display.isupper() else display
    return roster


# ---------------------------------------------------------------------------
# Pass 2: walk scene by scene
# ---------------------------------------------------------------------------

def build_scenes(root: ET.Element, roster: dict, location_roster: dict) -> list:
    content = root.find("Content")
    if content is None:
        return []

    roster_keys_sorted = sorted(roster.keys(), key=len, reverse=True)  # match longer names first
    scenes: list[ScenePayload] = []
    current: ScenePayload | None = None
    order_index = 0

    for paragraph in content.findall("Paragraph"):
        ptype = paragraph.get("Type", "")
        text = paragraph_text(paragraph)

        if ptype == "Scene Heading":
            order_index += 1
            props = paragraph.find("SceneProperties")
            length_raw = props.get("Length") if props is not None else None
            page_raw = props.get("Page") if props is not None else None
            ext_int, location, time_of_day = parse_slugline(text)
            location = location or "Unknown"
            current = ScenePayload(
                order_index=order_index,
                ext_int=ext_int,
                location=location,
                location_key=normalize_location(location),
                time_of_day=time_of_day or "UNSPECIFIED",
                page_length=parse_length_to_eighths(length_raw),
                page_number=int(page_raw) if page_raw and page_raw.isdigit() else None,
            )
            scenes.append(current)
            continue

        if current is None:
            continue  # content before the first scene heading (title page overflow, etc.)

        if ptype in BODY_TYPES and text.strip():
            current.raw_text_parts.append(text.strip())

        if ptype == "Character":
            key = normalize_name(text)
            if key in roster:
                current.confirmed_characters.add(key)

        if ptype == "Action" and text.strip():
            # Scan for mentions of any roster character (silent/background presence).
            for key in roster_keys_sorted:
                display = roster[key]
                pattern = re.compile(r"\b" + re.escape(display) + r"\b", re.IGNORECASE)
                if pattern.search(text):
                    current.auto_detected_characters.add(key)

    return scenes


# ---------------------------------------------------------------------------
# Save to DB
# ---------------------------------------------------------------------------

def save_to_db(script_id: int, roster: dict, location_roster: dict, scenes: list):
    init_db()
    session = SessionLocal()
    try:
        script = session.get(Script, script_id)
        if script is None:
            raise ValueError(f"No script found with id={script_id}. Run import_fdx.py first.")

        # Wipe any previously parsed scenes for this script (safe re-parse).
        for old_scene in list(script.scenes):
            session.delete(old_scene)
        session.commit()

        # Upsert characters into the global roster table.
        name_to_char = {}
        for key, display_name in roster.items():
            char = session.query(Character).filter_by(character_name=display_name).first()
            if char is None:
                char = Character(character_name=display_name, is_speaking=True)
                session.add(char)
                session.flush()
            name_to_char[key] = char

        # Upsert locations into the global location table. Case-insensitive
        # lookup here too, in case a location with different casing already
        # exists from a previous import (e.g. of a different draft).
        key_to_location = {}
        for key, display_name in location_roster.items():
            loc = session.query(Location).filter(Location.location_name.ilike(display_name)).first()
            if loc is None:
                loc = Location(location_name=display_name)
                session.add(loc)
                session.flush()
            key_to_location[key] = loc

        for payload in scenes:
            location = key_to_location.get(payload.location_key)
            if location is None:
                # Shouldn't happen (every scene's key came from location_roster),
                # but fall back to an "Unknown" bucket rather than fail the import.
                location = session.query(Location).filter(Location.location_name.ilike("Unknown")).first()
                if location is None:
                    location = Location(location_name="Unknown")
                    session.add(location)
                    session.flush()

            scene = Scene(
                script_id=script.id,
                scene_number=str(payload.order_index),
                order_index=payload.order_index,
                ext_int=payload.ext_int,
                location_id=location.location_id,
                time_of_day=payload.time_of_day,
                page_length=payload.page_length,
                page_number=payload.page_number,
                raw_text=payload.raw_text,
            )
            session.add(scene)
            session.flush()

            # Confirmed (spoke via a Character cue) takes priority over auto-detected
            # if a character both speaks AND is separately mentioned in action text.
            auto_only = payload.auto_detected_characters - payload.confirmed_characters
            for key in payload.confirmed_characters:
                char = name_to_char.get(key)
                if char:
                    session.add(SceneCharacter(scene_id=scene.id, character_id=char.character_id,
                                                is_auto_detected=False))
            for key in auto_only:
                char = name_to_char.get(key)
                if char:
                    session.add(SceneCharacter(scene_id=scene.id, character_id=char.character_id,
                                                is_auto_detected=True))

        session.commit()
        return len(scenes), len(roster)
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Parse a registered .fdx file into the database.")
    parser.add_argument("--script-id", type=int, required=True, help="script_id from import_fdx.py")
    args = parser.parse_args()

    init_db()
    session = SessionLocal()
    script = session.get(Script, args.script_id)
    session.close()
    if script is None:
        print(f"No script found with id={args.script_id}. Run import_fdx.py first.", file=sys.stderr)
        sys.exit(1)

    root = ET.parse(script.stored_path).getroot()
    roster = build_roster(root)
    location_roster = build_location_roster(root)
    scenes = build_scenes(root, roster, location_roster)
    scene_count, char_count = save_to_db(args.script_id, roster, location_roster, scenes)

    print(f"Parsed '{script.title}':")
    print(f"  {scene_count} scenes")
    print(f"  {char_count} characters")
    print(f"  {len(location_roster)} locations")
    print("Next step: python api.py")


if __name__ == "__main__":
    main()
