"""
models.py
---------
Shared SQLAlchemy table definitions and database session setup for the
screenplay production-analysis app.

This is not one of the four requested scripts — it's the schema definition
both parse_and_store.py (writes) and api.py (reads/writes) import, so the
table structure exists in exactly one place instead of being duplicated.

Restructured schema notes vs. the original draft:
- Added `script` table + `scene.script_id` so more than one screenplay
  (or re-import of a revised draft) can live in the same database.
- Added `scene.scene_number`, `scene.order_index`, `scene.page_length`,
  `scene.page_number`, `scene.raw_text` (see prior architecture discussion).
- Added `character.is_speaking` and `scene_character.is_auto_detected` to
  distinguish characters found via dialogue attribution vs. characters only
  mentioned in action text.
- Dropped `is_auto_detected` from scene_prop / scene_vehicle / scene_costume:
  per your instruction, props/vehicles/costumes are added manually from the
  UI only, so there is no "auto-detected" state to track for them.
- Added a `location` table + `scene.location_id` FK (previously `scene`
  stored location as free text). Locations are normalized/deduped the same
  way characters are, including unifying curly vs. straight apostrophes
  ("Rajesh's" vs "Rajesh's") which plain case-folding alone doesn't catch.
"""
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Text, Boolean, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMPORTS_DIR = DATA_DIR / "imports"
DATA_DIR.mkdir(exist_ok=True)
IMPORTS_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "production.db"

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Script(Base):
    __tablename__ = "script"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    source_filename = Column(String, nullable=True)
    stored_path = Column(String, nullable=True)   # where the .fdx was copied to on import
    imported_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    revision_color = Column(String, nullable=True)
    revision_date = Column(String, nullable=True)

    scenes = relationship("Scene", back_populates="script", cascade="all, delete-orphan")


class Scene(Base):
    __tablename__ = "scene"

    id = Column(Integer, primary_key=True)
    script_id = Column(Integer, ForeignKey("script.id", ondelete="CASCADE"), nullable=False)
    scene_number = Column(String, nullable=True)      # editable label, e.g. "12A"
    order_index = Column(Integer, nullable=False)      # true sequential position
    ext_int = Column(String, nullable=False)            # INT / EXT / INT/EXT
    location_id = Column(Integer, ForeignKey("location.location_id"), nullable=False)
    time_of_day = Column(String, nullable=False)
    page_length = Column(Float, nullable=True)           # decimal eighths, e.g. 1.875
    page_number = Column(Integer, nullable=True)          # starting page, from FDX
    raw_text = Column(Text, nullable=True)                 # full action+dialogue text
    description = Column(String, nullable=True)
    production_notes = Column(String, nullable=True)
    sfx_notes = Column(String, nullable=True)

    script = relationship("Script", back_populates="scenes")
    location = relationship("Location")
    characters = relationship("SceneCharacter", back_populates="scene", cascade="all, delete-orphan")
    props = relationship("SceneProp", back_populates="scene", cascade="all, delete-orphan")
    vehicles = relationship("SceneVehicle", back_populates="scene", cascade="all, delete-orphan")
    costumes = relationship("SceneCostume", back_populates="scene", cascade="all, delete-orphan")


class Location(Base):
    __tablename__ = "location"
    location_id = Column(Integer, primary_key=True)
    location_name = Column(String, nullable=False, unique=True)


class Character(Base):
    __tablename__ = "character"

    character_id = Column(Integer, primary_key=True)
    character_name = Column(String, nullable=False, unique=True)
    is_speaking = Column(Boolean, nullable=False, default=True)


class Prop(Base):
    __tablename__ = "prop"
    prop_id = Column(Integer, primary_key=True)
    prop_name = Column(String, nullable=False, unique=True)


class Vehicle(Base):
    __tablename__ = "vehicle"
    vehicle_id = Column(Integer, primary_key=True)
    vehicle_name = Column(String, nullable=False, unique=True)


class Costume(Base):
    __tablename__ = "costume"
    costume_id = Column(Integer, primary_key=True)
    costume_name = Column(String, nullable=False, unique=True)


class SceneCharacter(Base):
    __tablename__ = "scene_character"
    scene_id = Column(Integer, ForeignKey("scene.id", ondelete="CASCADE"), primary_key=True)
    character_id = Column(Integer, ForeignKey("character.character_id", ondelete="CASCADE"), primary_key=True)
    is_auto_detected = Column(Boolean, nullable=False, default=False)  # True = found via action-text scan only

    scene = relationship("Scene", back_populates="characters")
    character = relationship("Character")


class SceneProp(Base):
    __tablename__ = "scene_prop"
    scene_id = Column(Integer, ForeignKey("scene.id", ondelete="CASCADE"), primary_key=True)
    prop_id = Column(Integer, ForeignKey("prop.prop_id", ondelete="CASCADE"), primary_key=True)

    scene = relationship("Scene", back_populates="props")
    prop = relationship("Prop")


class SceneVehicle(Base):
    __tablename__ = "scene_vehicle"
    scene_id = Column(Integer, ForeignKey("scene.id", ondelete="CASCADE"), primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicle.vehicle_id", ondelete="CASCADE"), primary_key=True)

    scene = relationship("Scene", back_populates="vehicles")
    vehicle = relationship("Vehicle")


class SceneCostume(Base):
    __tablename__ = "scene_costume"
    scene_id = Column(Integer, ForeignKey("scene.id", ondelete="CASCADE"), primary_key=True)
    costume_id = Column(Integer, ForeignKey("costume.costume_id", ondelete="CASCADE"), primary_key=True)

    scene = relationship("Scene", back_populates="costumes")
    costume = relationship("Costume")


def init_db():
    """Create all tables if they don't already exist."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print(f"Database ready at {DB_PATH}")
