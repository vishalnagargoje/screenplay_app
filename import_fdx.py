"""
import_fdx.py
-------------
SCRIPT 1 of 4.

Validates a Final Draft (.fdx) file and registers it in the database as a
`script` row. Copies the file into ./data/imports/ so parse_and_store.py
always has a stable local path to work from, even if the original file
moves or is deleted.

This script does NOT parse scenes/characters — see parse_and_store.py for
that. Its only job is: "is this a real FDX file, and let's log it."

Usage:
    python import_fdx.py "When_Strangers_meet.fdx"
    python import_fdx.py "When_Strangers_meet.fdx" --title "When Strangers Meet"

Prints the new script_id on success, which you then pass to
parse_and_store.py:
    python parse_and_store.py --script-id 1
"""
import argparse
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from models import Script, SessionLocal, init_db


def validate_fdx(path: Path) -> ET.Element:
    """Confirm the file is well-formed XML with a <FinalDraft> root.
    Returns the parsed root element, or raises ValueError with a clear reason."""
    if path.suffix.lower() != ".fdx":
        raise ValueError(f"'{path.name}' does not have a .fdx extension.")
    if not path.exists():
        raise ValueError(f"File not found: {path}")

    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise ValueError(f"'{path.name}' is not well-formed XML: {e}")

    root = tree.getroot()
    if root.tag != "FinalDraft":
        raise ValueError(
            f"'{path.name}' does not look like a Final Draft file "
            f"(expected root tag <FinalDraft>, found <{root.tag}>)."
        )
    if root.find("Content") is None:
        raise ValueError(f"'{path.name}' has no <Content> section — nothing to parse.")

    return root


def import_fdx_file(source_path: str, title: str | None = None) -> int:
    """Validate, copy, and register an FDX file. Returns the new script_id."""
    path = Path(source_path).expanduser().resolve()
    root = validate_fdx(path)

    # Pull whatever revision metadata Final Draft stored, if any.
    doc_ref = root.find("DocumentRef")
    revision_date = doc_ref.get("DateTime") if doc_ref is not None else None

    # Stage a copy so later steps don't depend on the original file's location.
    from models import IMPORTS_DIR
    stored_name = f"{uuid.uuid4().hex}_{path.name}"
    stored_path = IMPORTS_DIR / stored_name
    shutil.copy2(path, stored_path)

    init_db()
    session = SessionLocal()
    try:
        script = Script(
            title=title or path.stem.replace("_", " ").strip(),
            source_filename=path.name,
            stored_path=str(stored_path),
            revision_date=revision_date,
        )
        session.add(script)
        session.commit()
        session.refresh(script)
        return script.id
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Import and register a Final Draft (.fdx) file.")
    parser.add_argument("file", help="Path to the .fdx file")
    parser.add_argument("--title", help="Screenplay title (defaults to filename)", default=None)
    args = parser.parse_args()

    try:
        script_id = import_fdx_file(args.file, args.title)
    except ValueError as e:
        print(f"Import failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Imported successfully. script_id = {script_id}")
    print(f"Next step: python parse_and_store.py --script-id {script_id}")


if __name__ == "__main__":
    main()
