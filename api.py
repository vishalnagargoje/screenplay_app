"""
api.py
------
SCRIPT 3 of 4.

FastAPI REST server exposing the production-analysis database. Runs
entirely on your local machine.

Props, vehicles, and costumes are managed ENTIRELY through this API by the
user (not auto-extracted) — the POST endpoints below do "find existing by
name, or create it" in one call so the frontend's "add prop/vehicle/costume"
box can just send a name string.

Usage:
    python api.py
    -> serves on http://127.0.0.1:8000
    -> interactive docs at http://127.0.0.1:8000/docs
"""
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import joinedload

from models import (
    Script, Scene, Character, Location, Prop, Vehicle, Costume,
    SceneCharacter, SceneProp, SceneVehicle, SceneCostume,
    SessionLocal, init_db,
)

app = FastAPI(title="Screenplay Production Analysis API")

# Local-only tool: allow the local frontend (interface.py, a different port)
# to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SceneUpdate(BaseModel):
    scene_number: Optional[str] = None
    ext_int: Optional[str] = None
    location: Optional[str] = None  # a name; resolved to/created in the location table below
    time_of_day: Optional[str] = None
    description: Optional[str] = None
    production_notes: Optional[str] = None
    sfx_notes: Optional[str] = None


class NamedEntityIn(BaseModel):
    name: str


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def serialize_scene(scene: Scene, include_text: bool = False) -> dict:
    data = {
        "id": scene.id,
        "script_id": scene.script_id,
        "scene_number": scene.scene_number,
        "order_index": scene.order_index,
        "ext_int": scene.ext_int,
        "location_id": scene.location_id,
        "location_name": scene.location.location_name if scene.location else None,
        "time_of_day": scene.time_of_day,
        "page_length": scene.page_length,
        "page_number": scene.page_number,
        "description": scene.description,
        "production_notes": scene.production_notes,
        "sfx_notes": scene.sfx_notes,
        "characters": [
            {
                "character_id": sc.character.character_id,
                "character_name": sc.character.character_name,
                "is_auto_detected": sc.is_auto_detected,
            }
            for sc in scene.characters
        ],
        "props": [{"prop_id": sp.prop.prop_id, "prop_name": sp.prop.prop_name} for sp in scene.props],
        "vehicles": [{"vehicle_id": sv.vehicle.vehicle_id, "vehicle_name": sv.vehicle.vehicle_name} for sv in scene.vehicles],
        "costumes": [{"costume_id": sc2.costume.costume_id, "costume_name": sc2.costume.costume_name} for sc2 in scene.costumes],
    }
    if include_text:
        data["raw_text"] = scene.raw_text
    return data


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------

@app.get("/api/scripts")
def list_scripts():
    session = SessionLocal()
    try:
        scripts = session.query(Script).all()
        return [
            {
                "id": s.id,
                "title": s.title,
                "source_filename": s.source_filename,
                "imported_at": s.imported_at.isoformat() if s.imported_at else None,
                "scene_count": len(s.scenes),
            }
            for s in scripts
        ]
    finally:
        session.close()


@app.get("/api/scripts/{script_id}")
def get_script(script_id: int):
    session = SessionLocal()
    try:
        script = session.get(Script, script_id)
        if not script:
            raise HTTPException(404, "Script not found")
        return {
            "id": script.id,
            "title": script.title,
            "source_filename": script.source_filename,
            "imported_at": script.imported_at.isoformat() if script.imported_at else None,
            "scene_count": len(script.scenes),
            "character_count": session.query(Character).count(),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

@app.get("/api/scenes")
def list_scenes(script_id: Optional[int] = None):
    session = SessionLocal()
    try:
        query = session.query(Scene).options(
            joinedload(Scene.location),
            joinedload(Scene.characters).joinedload(SceneCharacter.character),
            joinedload(Scene.props).joinedload(SceneProp.prop),
            joinedload(Scene.vehicles).joinedload(SceneVehicle.vehicle),
            joinedload(Scene.costumes).joinedload(SceneCostume.costume),
        )
        if script_id is not None:
            query = query.filter(Scene.script_id == script_id)
        scenes = query.order_by(Scene.order_index).all()
        return [serialize_scene(s) for s in scenes]
    finally:
        session.close()


@app.get("/api/scenes/{scene_id}")
def get_scene(scene_id: int):
    session = SessionLocal()
    try:
        scene = session.get(Scene, scene_id)
        if not scene:
            raise HTTPException(404, "Scene not found")
        return serialize_scene(scene, include_text=True)
    finally:
        session.close()


@app.patch("/api/scenes/{scene_id}")
def update_scene(scene_id: int, payload: SceneUpdate):
    session = SessionLocal()
    try:
        scene = session.get(Scene, scene_id)
        if not scene:
            raise HTTPException(404, "Scene not found")

        fields = payload.dict(exclude_unset=True)

        # "location" is a name, not a column — resolve it against the location
        # table (case-insensitive) and point the scene at that row, creating
        # it if it doesn't exist yet. This is how renaming/merging locations
        # from the UI works: rename a scene's location to an existing name
        # (any casing) and it joins that location instead of creating a new one.
        if "location" in fields:
            name = (fields.pop("location") or "").strip()
            if not name:
                raise HTTPException(400, "Location name cannot be empty")
            loc = session.query(Location).filter(Location.location_name.ilike(name)).first()
            if not loc:
                loc = Location(location_name=name)
                session.add(loc)
                session.flush()
            scene.location_id = loc.location_id

        for field, value in fields.items():
            setattr(scene, field, value)

        session.commit()
        session.refresh(scene)
        return serialize_scene(scene, include_text=True)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

@app.get("/api/locations")
def list_locations(script_id: Optional[int] = None):
    session = SessionLocal()
    try:
        locations = session.query(Location).order_by(Location.location_name).all()
        result = []
        for loc in locations:
            q = session.query(Scene).filter(Scene.location_id == loc.location_id)
            if script_id is not None:
                q = q.filter(Scene.script_id == script_id)
            scene_count = q.count()
            if script_id is not None and scene_count == 0:
                continue
            result.append({
                "location_id": loc.location_id,
                "location_name": loc.location_name,
                "scene_count": scene_count,
            })
        return result
    finally:
        session.close()


@app.post("/api/locations")
def create_location(payload: NamedEntityIn):
    """Find-or-create by name (case-insensitive) — same pattern as
    props/vehicles/costumes. Mainly useful if you want to pre-create a
    location before assigning it to a scene."""
    session = SessionLocal()
    try:
        name = payload.name.strip()
        if not name:
            raise HTTPException(400, "Name cannot be empty")
        existing = session.query(Location).filter(Location.location_name.ilike(name)).first()
        if existing:
            return {"location_id": existing.location_id, "location_name": existing.location_name}
        loc = Location(location_name=name)
        session.add(loc)
        session.commit()
        session.refresh(loc)
        return {"location_id": loc.location_id, "location_name": loc.location_name}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------

@app.get("/api/characters")
def list_characters(script_id: Optional[int] = None):
    session = SessionLocal()
    try:
        characters = session.query(Character).order_by(Character.character_name).all()
        result = []
        for c in characters:
            q = session.query(SceneCharacter).filter(SceneCharacter.character_id == c.character_id)
            if script_id is not None:
                q = q.join(Scene).filter(Scene.script_id == script_id)
            links = q.all()
            if script_id is not None and not links:
                continue
            result.append({
                "character_id": c.character_id,
                "character_name": c.character_name,
                "is_speaking": c.is_speaking,
                "scene_count": len(links),
            })
        return result
    finally:
        session.close()


@app.get("/api/characters/{character_id}")
def get_character(character_id: int):
    session = SessionLocal()
    try:
        c = session.get(Character, character_id)
        if not c:
            raise HTTPException(404, "Character not found")
        links = session.query(SceneCharacter).filter(SceneCharacter.character_id == character_id).all()
        return {
            "character_id": c.character_id,
            "character_name": c.character_name,
            "is_speaking": c.is_speaking,
            "scenes": [
                {
                    "scene_id": l.scene.id,
                    "scene_number": l.scene.scene_number,
                    "location_name": l.scene.location.location_name if l.scene.location else None,
                    "is_auto_detected": l.is_auto_detected,
                }
                for l in links
            ],
        }
    finally:
        session.close()


@app.delete("/api/scenes/{scene_id}/characters/{character_id}")
def unlink_character(scene_id: int, character_id: int):
    session = SessionLocal()
    try:
        link = session.get(SceneCharacter, (scene_id, character_id))
        if not link:
            raise HTTPException(404, "Link not found")
        session.delete(link)
        session.commit()
        return {"status": "removed"}
    finally:
        session.close()


@app.post("/api/scenes/{scene_id}/characters/{character_id}")
def link_character(scene_id: int, character_id: int):
    session = SessionLocal()
    try:
        if not session.get(Scene, scene_id):
            raise HTTPException(404, "Scene not found")
        if not session.get(Character, character_id):
            raise HTTPException(404, "Character not found")
        existing = session.get(SceneCharacter, (scene_id, character_id))
        if not existing:
            session.add(SceneCharacter(scene_id=scene_id, character_id=character_id, is_auto_detected=False))
            session.commit()
        return {"status": "linked"}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Generic helper for props / vehicles / costumes
# (all three follow an identical "global list + per-scene link" pattern)
# ---------------------------------------------------------------------------

ENTITY_CONFIG = {
    "props": {"model": Prop, "id_field": "prop_id", "name_field": "prop_name", "link_model": SceneProp},
    "vehicles": {"model": Vehicle, "id_field": "vehicle_id", "name_field": "vehicle_name", "link_model": SceneVehicle},
    "costumes": {"model": Costume, "id_field": "costume_id", "name_field": "costume_name", "link_model": SceneCostume},
}


def register_entity_routes(kind: str):
    cfg = ENTITY_CONFIG[kind]
    Model, id_field, name_field, LinkModel = cfg["model"], cfg["id_field"], cfg["name_field"], cfg["link_model"]

    @app.get(f"/api/{kind}", name=f"list_{kind}")
    def list_entities():
        session = SessionLocal()
        try:
            items = session.query(Model).order_by(getattr(Model, name_field)).all()
            return [{id_field: getattr(i, id_field), name_field: getattr(i, name_field)} for i in items]
        finally:
            session.close()

    @app.post(f"/api/{kind}", name=f"create_{kind}")
    def create_entity(payload: NamedEntityIn):
        session = SessionLocal()
        try:
            name = payload.name.strip()
            if not name:
                raise HTTPException(400, "Name cannot be empty")
            existing = session.query(Model).filter(getattr(Model, name_field).ilike(name)).first()
            if existing:
                return {id_field: getattr(existing, id_field), name_field: getattr(existing, name_field)}
            item = Model(**{name_field: name})
            session.add(item)
            session.commit()
            session.refresh(item)
            return {id_field: getattr(item, id_field), name_field: getattr(item, name_field)}
        finally:
            session.close()

    @app.post(f"/api/scenes/{{scene_id}}/{kind}", name=f"add_{kind}_to_scene")
    def add_to_scene(scene_id: int, payload: NamedEntityIn):
        session = SessionLocal()
        try:
            scene = session.get(Scene, scene_id)
            if not scene:
                raise HTTPException(404, "Scene not found")
            name = payload.name.strip()
            if not name:
                raise HTTPException(400, "Name cannot be empty")
            entity = session.query(Model).filter(getattr(Model, name_field).ilike(name)).first()
            if not entity:
                entity = Model(**{name_field: name})
                session.add(entity)
                session.flush()
            entity_id = getattr(entity, id_field)
            link_key = (scene_id, entity_id)
            if not session.get(LinkModel, link_key):
                session.add(LinkModel(scene_id=scene_id, **{id_field: entity_id}))
            session.commit()
            return {id_field: entity_id, name_field: getattr(entity, name_field)}
        finally:
            session.close()

    @app.delete(f"/api/scenes/{{scene_id}}/{kind}/{{entity_id}}", name=f"remove_{kind}_from_scene")
    def remove_from_scene(scene_id: int, entity_id: int):
        session = SessionLocal()
        try:
            link = session.get(LinkModel, (scene_id, entity_id))
            if not link:
                raise HTTPException(404, "Link not found")
            session.delete(link)
            session.commit()
            return {"status": "removed"}
        finally:
            session.close()


for kind in ENTITY_CONFIG:
    register_entity_routes(kind)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=False)
