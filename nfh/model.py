"""Typed access to the converted game data (levels, rooms, objects, animations ...)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import data
from .node import Node

Pt = Tuple[int, ...]


# ---------------------------------------------------------------- helpers
def pt(s: Optional[str]) -> Optional[Pt]:
    """'12/-4' -> (12, -4);  '255/0/0' -> (255, 0, 0)."""
    if s is None:
        return None
    return tuple(int(p) for p in s.split("/"))


def as_int(s, default=0) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def as_bool(s, default=False) -> bool:
    return default if s is None else s.strip().lower() in ("true", "1", "yes")


def _root(path: str, tag: str) -> List[Node]:
    out = []
    for r in data.load(path):
        out.extend([r] if r.tag == tag else r.findall(tag))
    return out


def _children(path: str, wrapper: str) -> List[Node]:
    """Children of <wrapper>, or the top-level nodes when the file has no wrapper."""
    roots = data.load(path)
    if len(roots) == 1 and roots[0].tag == wrapper:
        return list(roots[0])
    return list(roots)


# ------------------------------------------------------------ level order
@dataclass
class LevelInfo:
    name: str
    state: str
    reachable: int
    minquota: int
    time: int


@dataclass
class LevelSet:
    name: str
    state: str
    nextset: Optional[str]
    levels: List[LevelInfo]


def level_sets() -> List[LevelSet]:
    sets = []
    for s in _children("leveldata", "leveldata"):
        sets.append(LevelSet(
            s["name"], s.get("state", ""), s.get("nextset"),
            [LevelInfo(l["name"], l.get("state", ""), as_int(l.get("reachable")),
                       as_int(l.get("minquota")), as_int(l.get("time")))
             for l in s.findall("level")]))
    return _with_unlisted(sets)


def _with_unlisted(sets: List["LevelSet"]) -> List["LevelSet"]:
    """A leveldata.xml that lists fewer levels than the data folder holds (test builds) hides them; levels
    found in the data source but missing from the list are added to their set from the bundled leveldata."""
    src = getattr(data, "_source", None)
    if src is None:
        return sets
    have = {l.name.lower() for s in sets for l in s.levels}
    try:
        from .data import leveldata as bundled
        ref = {l["name"].lower(): (s["name"], l) for s in bundled.ROOTS[0] for l in s.findall("level")}
    except ImportError:
        ref = {}
    by_name = {s.name: s for s in sets}
    for d in src.level_dirs():
        if d.lower() in have:
            continue
        set_name, l = ref.get(d.lower(), ("set01", None))
        target = by_name.get(set_name) or sets[0]
        info = LevelInfo(d, l.get("state", "playable") if l else "playable", as_int(l.get("reachable")) if l else 0,
                         as_int(l.get("minquota"), 60) if l else 60, as_int(l.get("time"), 3600) if l else 3600)
        info.state = "playable"                       # the data is there, so it can be played
        target.levels.append(info)
    return sets


# ------------------------------------------------------------ level layout
@dataclass
class Floor:
    offset: Pt
    size: Pt
    wall: bool
    hotspot: Optional[Pt]


@dataclass
class Placement:
    """An object / door / actor placed in a room (or in the whole level)."""
    kind: str
    name: str
    layer: int
    position: Optional[Pt]
    visible: bool
    animation: Optional[str] = None


@dataclass
class Neighbor:
    name: str
    costs: int
    doorin: str
    doorout: str


@dataclass
class Room:
    name: str
    offset: Pt
    path1: Pt
    path2: Pt
    floors: List[Floor] = field(default_factory=list)
    placements: List[Placement] = field(default_factory=list)
    neighbors: List[Neighbor] = field(default_factory=list)

    def of_kind(self, kind: str) -> List[Placement]:
        return [p for p in self.placements if p.kind == kind]


@dataclass
class Level:
    name: str
    size: Pt
    angrytime: int
    placements: List[Placement]
    rooms: Dict[str, Room]


def _placement(e: Node) -> Placement:
    return Placement(e.tag, e["name"], as_int(e.get("layer")), pt(e.get("position")),
                     as_bool(e.get("visible"), True), e.get("animation"))


def load_layout(level_dir: str) -> Level:
    root = data.load(f"{level_dir}/level")[0]
    lvl = Level(root["name"], pt(root.get("size")), as_int(root.get("angrytime")), [], {})
    for e in root:
        if e.tag == "object":
            lvl.placements.append(_placement(e))
        elif e.tag == "room":
            room = Room(e["name"], pt(e["offset"]), pt(e.get("path1")), pt(e.get("path2")))
            for c in e:
                if c.tag == "floor":
                    room.floors.append(Floor(pt(c["offset"]), pt(c["size"]),
                                             as_bool(c.get("wall")), pt(c.get("hotspot"))))
                elif c.tag in ("object", "door", "actor"):
                    room.placements.append(_placement(c))
                elif c.tag == "neighbor":
                    room.neighbors.append(Neighbor(c["name"], as_int(c.get("costs")),
                                                   c.get("doorin", ""), c.get("doorout", "")))
            lvl.rooms[room.name] = room
    return lvl


# ------------------------------------------------------- object definitions
@dataclass
class ObjectDef:
    kind: str                      # object | door | actor | icon | inventar
    name: str
    gfx: Optional[str]
    hotspot: Optional[Pt]
    attrs: Dict[str, str]
    hotspots: Dict[str, Pt] = field(default_factory=dict)
    speeds: Dict[str, Dict[str, str]] = field(default_factory=dict)
    actions: List[Dict[str, str]] = field(default_factory=list)
    stdactions: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    contents: List[Dict[str, str]] = field(default_factory=list)
    images: Dict[str, str] = field(default_factory=dict)     # inventory icons


def _object_defs(dir_: str, kinds) -> Dict[str, ObjectDef]:
    out: Dict[str, ObjectDef] = {}
    for e in _children(f"{dir_}/objects", "objects"):
        if e.tag not in kinds:
            continue
        d = ObjectDef(e.tag, e["name"], e.get("gfx"), pt(e.get("hotspot")), dict(e.attrs))
        for c in e:
            if c.tag == "hotspot":
                d.hotspots[c["name"]] = pt(c["offset"])
            elif c.tag == "speed":
                d.speeds[c["name"]] = dict(c.attrs)
            elif c.tag == "action":
                d.actions.append(dict(c.attrs))
            elif c.tag == "stdaction":
                d.stdactions.append(c["name"])
            elif c.tag == "flag":
                d.flags.append(c["name"])
            elif c.tag == "content":
                d.contents.append(dict(c.attrs))
            elif c.tag == "image":
                d.images[c["name"]] = c["gfx"]
        out[e["name"]] = d
    return out


def load_objects(dir_: str) -> Dict[str, ObjectDef]:
    """Objects, doors and actors (inventory items / icons share names, see load_inventory)."""
    return _object_defs(dir_, ("object", "door", "actor"))


def load_inventory(dir_: str) -> Dict[str, ObjectDef]:
    """Inventory items ('inventar') and speech-bubble icons."""
    return _object_defs(dir_, ("inventar", "icon"))


# -------------------------------------------------------------- animations
@dataclass
class Frame:
    gfx: Optional[str]              # None for frames that only trigger a sound / delay
    sfx: Optional[str]


@dataclass
class Animation:
    name: str
    type: str                       # loop | oneshot ...
    frames: List[Frame]


@dataclass
class Region:
    position: Pt
    size: Pt
    type: Optional[str]


@dataclass
class AnimObject:
    name: str
    regions: List[Region]
    animations: Dict[str, Animation]


def load_anims(dir_: str) -> Dict[str, AnimObject]:
    out: Dict[str, AnimObject] = {}
    for o in _children(f"{dir_}/anims", "all_objects"):
        ao = AnimObject(o["name"], [], {})
        for c in o:
            if c.tag == "region":
                ao.regions.append(Region(pt(c["position"]), pt(c["size"]), c.get("type")))
            elif c.tag == "animation":
                ao.animations[c["name"]] = Animation(
                    c["name"], c.get("type", ""),
                    [Frame(f.get("gfx"), f.get("sfx")) for f in c.findall("frame")])
        out[ao.name] = ao
    return out


# ---------------------------------------------------- sprites and sounds
@dataclass
class GfxFile:
    image: str
    offset: Pt


def load_gfxdata(dir_: str) -> Dict[str, List[GfxFile]]:
    out: Dict[str, List[GfxFile]] = {}
    for o in _children(f"{dir_}/gfxdata", "__none__"):
        files = [GfxFile(f["image"], pt(f["offset"])) for g in o.findall("gfxdata")
                 for f in g.findall("file")]
        out.setdefault(o["name"], []).extend(files)
    return out


def load_sfxdata(dir_: str) -> List[Tuple[str, Optional[str]]]:
    return [(s["file"], s.get("volume")) for s in _children(f"{dir_}/sfxdata", "sfxdata")]


# ------------------------------------------------- strings / tricks / logic
def load_strings(path: str) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for s in _children(path, "strings"):
        if s.tag == "string":
            out.setdefault(s["name"], {})[s.get("category", "")] = s.get("text", "")
    return out


@dataclass
class Trick:
    name: str
    quotas: Tuple[int, int, int, int]
    angrytime: Optional[int]


def load_tricks(dir_: str) -> Dict[str, Trick]:
    return {t["name"]: Trick(t["name"], tuple(as_int(t.get(f"quota{i}")) for i in range(1, 5)),
                             as_int(t.get("angrytime")) if t.get("angrytime") else None)
            for t in _children(f"{dir_}/tricks", "tricks") if t.tag == "trick"}


@dataclass
class Combination:
    name: str
    trick: bool
    wrong: bool
    animation: Optional[str]
    layer: Optional[int]
    ingredients: List[Tuple[str, bool]]          # (name, removed after use)


def load_combinations(dir_: str) -> List[Combination]:
    return [Combination(c["name"], as_bool(c.get("trick")), as_bool(c.get("wrong")),
                        c.get("animation"), as_int(c["layer"]) if c.get("layer") else None,
                        [(i["name"], as_bool(i.get("remove"))) for i in c.findall("ingredient")])
            for c in _children(f"{dir_}/combine", "combine") if c.tag == "combination"]


@dataclass
class Trigger:
    attrs: Dict[str, str]


def load_triggers(dir_: str) -> Dict[str, Dict[str, List[Trigger]]]:
    """actor -> behaviour name -> list of triggers."""
    out: Dict[str, Dict[str, List[Trigger]]] = {}
    for a in _children(f"{dir_}/trigger", "triggers"):
        beh = out.setdefault(a["name"], {})
        for b in a.findall("behavior"):
            beh.setdefault(b["name"], []).extend(Trigger(dict(t.attrs)) for t in b.findall("trigger"))
    return out


# ------------------------------------------------------------- whole level
@dataclass
class LevelData:
    name: str
    layout: Optional[Level]
    objects: Dict[str, ObjectDef]
    inventory: Dict[str, ObjectDef]
    anims: Dict[str, AnimObject]
    gfx: Dict[str, List[GfxFile]]
    sfx: List[Tuple[str, Optional[str]]]
    strings: Dict[str, Dict[str, str]]
    tricks: Dict[str, Trick]
    combinations: List[Combination]
    triggers: Dict[str, Dict[str, List[Trigger]]]

    def text(self, name: str, category: str = "name") -> Optional[str]:
        return self.strings.get(name, {}).get(category)


def _merge_object(base: ObjectDef, extra: ObjectDef) -> ObjectDef:
    base.attrs.update(extra.attrs)
    base.gfx = extra.gfx or base.gfx
    base.hotspot = extra.hotspot or base.hotspot
    base.hotspots.update(extra.hotspots)
    base.speeds.update(extra.speeds)
    base.actions.extend(extra.actions)
    for lst in ("stdactions", "flags"):
        getattr(base, lst).extend(x for x in getattr(extra, lst) if x not in getattr(base, lst))
    base.contents.extend(extra.contents)
    base.images.update(extra.images)
    return base


def load_level(name: str) -> LevelData:
    """Load a level folder (e.g. 'level_pig') merged on top of the `generic` data."""
    def has(p):
        try:
            data.load(p)
            return True
        except ModuleNotFoundError:
            return False

    objects = load_objects("generic")
    for k, d in load_objects(name).items():          # level entries extend the generic ones
        objects[k] = _merge_object(objects[k], d) if k in objects else d
    inventory = load_inventory("generic")
    inventory.update(load_inventory(name))
    anims = load_anims("generic")
    for k, a in load_anims(name).items():
        if k in anims:
            anims[k].regions.extend(a.regions)
            anims[k].animations.update(a.animations)
        else:
            anims[k] = a
    gfx = load_gfxdata("generic")
    for k, v in load_gfxdata(name).items():
        gfx.setdefault(k, []).extend(v)
    strings = load_strings("generic/strings")
    for k, v in load_strings(f"{name}/strings").items():
        strings.setdefault(k, {}).update(v)
    triggers = load_triggers("generic")
    for actor, beh in load_triggers(name).items():
        for b, ts in beh.items():
            triggers.setdefault(actor, {}).setdefault(b, []).extend(ts)
    return LevelData(
        name, load_layout(name) if has(f"{name}/level") else None,
        objects, inventory, anims, gfx,
        load_sfxdata("generic") + load_sfxdata(name),
        strings, load_tricks(name), load_combinations(name), triggers)


def level_names() -> List[str]:
    return [l.name for s in level_sets() for l in s.levels]
