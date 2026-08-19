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


class RenameIn(BaseModel):
    """Body for PATCH rename endpoints. Renaming an entity to a name that
    already belongs to a different row of the same kind (case-insensitive)
    merges the two: every scene linked to the old row is relinked to the
    existing one and the old row is deleted. This is the same "rename to
    merge" behavior the location field on a scene already had — extended
    here to the entities' own management endpoints, and to characters,
    props, and vehicles too."""
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


@app.patch("/api/locations/{location_id}")
def rename_location(location_id: int, payload: RenameIn):
    """Rename a location directly (affects every scene that uses it, unlike
    the per-scene location field on PATCH /api/scenes/{id}, which only
    repoints that one scene). Renaming to a name that matches another
    location merges the two: every scene pointed at this row is repointed
    at the existing one, and this row is deleted."""
    session = SessionLocal()
    try:
        loc = session.get(Location, location_id)
        if not loc:
            raise HTTPException(404, "Location not found")
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(400, "Name cannot be empty")

        existing = session.query(Location).filter(
            Location.location_name.ilike(new_name),
            Location.location_id != location_id,
        ).first()

        if existing:
            _merge_locations(session, loc, existing)
            session.commit()
            session.refresh(existing)
            return {"merged": True, "location_id": existing.location_id, "location_name": existing.location_name}

        loc.location_name = new_name
        session.commit()
        session.refresh(loc)
        return {"merged": False, "location_id": loc.location_id, "location_name": loc.location_name}
    finally:
        session.close()


@app.post("/api/locations/{location_id}/merge/{target_id}")
def merge_location(location_id: int, target_id: int):
    if location_id == target_id:
        raise HTTPException(400, "Cannot merge a location into itself")
    session = SessionLocal()
    try:
        loc = session.get(Location, location_id)
        target = session.get(Location, target_id)
        if not loc or not target:
            raise HTTPException(404, "Location not found")
        _merge_locations(session, loc, target)
        session.commit()
        session.refresh(target)
        return {"merged": True, "location_id": target.location_id, "location_name": target.location_name}
    finally:
        session.close()


def _merge_locations(session, source: Location, target: Location):
    session.query(Scene).filter(Scene.location_id == source.location_id).update(
        {Scene.location_id: target.location_id}
    )
    session.flush()
    session.delete(source)


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


@app.patch("/api/characters/{character_id}")
def rename_character(character_id: int, payload: RenameIn):
    """Rename a character. If the new name matches another character
    (case-insensitive), the two are merged into that existing character
    instead: every scene link moves over (a confirmed/speaking link always
    wins over an auto-detected one for a scene both share), and this row
    is deleted."""
    session = SessionLocal()
    try:
        entity = session.get(Character, character_id)
        if not entity:
            raise HTTPException(404, "Character not found")
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(400, "Name cannot be empty")

        existing = session.query(Character).filter(
            Character.character_name.ilike(new_name),
            Character.character_id != character_id,
        ).first()

        if existing:
            _merge_characters(session, entity, existing)
            session.commit()
            session.refresh(existing)
            return {"merged": True, "character_id": existing.character_id, "character_name": existing.character_name}

        entity.character_name = new_name
        session.commit()
        session.refresh(entity)
        return {"merged": False, "character_id": entity.character_id, "character_name": entity.character_name}
    finally:
        session.close()


@app.post("/api/characters/{character_id}/merge/{target_id}")
def merge_character(character_id: int, target_id: int):
    """Explicitly merge one character into another by id (no renaming
    required) — used by the "Merge into…" control in the UI."""
    if character_id == target_id:
        raise HTTPException(400, "Cannot merge a character into itself")
    session = SessionLocal()
    try:
        entity = session.get(Character, character_id)
        target = session.get(Character, target_id)
        if not entity or not target:
            raise HTTPException(404, "Character not found")
        _merge_characters(session, entity, target)
        session.commit()
        session.refresh(target)
        return {"merged": True, "character_id": target.character_id, "character_name": target.character_name}
    finally:
        session.close()


def _merge_characters(session, source: Character, target: Character):
    links = session.query(SceneCharacter).filter(SceneCharacter.character_id == source.character_id).all()
    for link in links:
        dup = session.get(SceneCharacter, (link.scene_id, target.character_id))
        if dup:
            # A scene linked to both: keep the more confident link (confirmed beats auto-detected).
            if not link.is_auto_detected and dup.is_auto_detected:
                dup.is_auto_detected = False
        else:
            session.add(SceneCharacter(
                scene_id=link.scene_id,
                character_id=target.character_id,
                is_auto_detected=link.is_auto_detected,
            ))
        session.delete(link)
    session.flush()
    session.delete(source)


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
    def list_entities(script_id: Optional[int] = None):
        session = SessionLocal()
        try:
            items = session.query(Model).order_by(getattr(Model, name_field)).all()
            result = []
            for i in items:
                entity_id = getattr(i, id_field)
                q = session.query(LinkModel).filter(getattr(LinkModel, id_field) == entity_id)
                if script_id is not None:
                    q = q.join(Scene).filter(Scene.script_id == script_id)
                scene_count = q.count()
                if script_id is not None and scene_count == 0:
                    continue
                result.append({id_field: entity_id, name_field: getattr(i, name_field), "scene_count": scene_count})
            return result
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

    def _merge(session, source_id: int, target_id: int):
        links = session.query(LinkModel).filter(getattr(LinkModel, id_field) == source_id).all()
        for link in links:
            dup_key = (link.scene_id, target_id)
            if not session.get(LinkModel, dup_key):
                session.add(LinkModel(scene_id=link.scene_id, **{id_field: target_id}))
            session.delete(link)
        session.flush()
        session.delete(session.get(Model, source_id))

    @app.patch(f"/api/{kind}/{{entity_id}}", name=f"rename_{kind}")
    def rename_entity(entity_id: int, payload: RenameIn):
        """Rename an item, or merge it into an existing item of the same
        name (case-insensitive) — every scene using this item is relinked
        to the existing one and this row is deleted."""
        session = SessionLocal()
        try:
            entity = session.get(Model, entity_id)
            if not entity:
                raise HTTPException(404, "Not found")
            new_name = payload.name.strip()
            if not new_name:
                raise HTTPException(400, "Name cannot be empty")

            existing = session.query(Model).filter(
                getattr(Model, name_field).ilike(new_name),
                getattr(Model, id_field) != entity_id,
            ).first()

            if existing:
                target_id = getattr(existing, id_field)
                _merge(session, entity_id, target_id)
                session.commit()
                session.refresh(existing)
                return {"merged": True, id_field: target_id, name_field: getattr(existing, name_field)}

            setattr(entity, name_field, new_name)
            session.commit()
            session.refresh(entity)
            return {"merged": False, id_field: entity_id, name_field: getattr(entity, name_field)}
        finally:
            session.close()

    @app.post(f"/api/{kind}/{{entity_id}}/merge/{{target_id}}", name=f"merge_{kind}")
    def merge_entity(entity_id: int, target_id: int):
        """Explicitly merge one item into another by id."""
        if entity_id == target_id:
            raise HTTPException(400, "Cannot merge an item into itself")
        session = SessionLocal()
        try:
            entity = session.get(Model, entity_id)
            target = session.get(Model, target_id)
            if not entity or not target:
                raise HTTPException(404, "Not found")
            _merge(session, entity_id, target_id)
            session.commit()
            session.refresh(target)
            return {"merged": True, id_field: target_id, name_field: getattr(target, name_field)}
        finally:
            session.close()


for kind in ENTITY_CONFIG:
    register_entity_routes(kind)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=False)
