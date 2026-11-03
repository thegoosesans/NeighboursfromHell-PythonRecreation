"""Engine core: animated entities, Woody walking and going through doors.

No graphics library is needed here - `World.update(dt)` advances time and collects sound events,
`nfh.render.render_world` draws the result and `nfh.viewer` shows it in a pygame window.

Coordinates
-----------
* object / door sprite  : top-left = room.offset + placement.position + gfx file offset
* actor sprite          : top-left = room.offset + position - actor.hotspot + gfx file offset
  (the actor's `position` is the point between its feet, in room coordinates)
"""
from __future__ import annotations
from typing import Dict, Iterator, List, Optional, Tuple

from .model import LevelData, Placement, Room, load_level
from .assets import SpriteBank
from .neighbor import NeighborAI

ANIM_FPS = 12.0                 # animation steps per second (assumption: the XML does not say)


class Entity:
    """One thing on screen: an object, a door or an actor, playing an animation."""

    def __init__(self, world: "World", key: str, kind: str, name: str, room: Optional[str],
                 origin: Tuple[int, int], pos: Tuple[int, int], layer: int, order: int,
                 initial_anim: Optional[str] = None):
        self.world = world
        self.key = key
        self.kind = kind                        # object | door | actor
        self.name = name                        # e.g. "anc/ark", "anc/kit", "woody"
        d = world.level.objects.get(name)
        self.cls = (d.gfx if d and d.gfx else name)      # animation / sprite class
        self.objdef = d
        self.room = room
        self.origin = origin
        self.x, self.y = pos
        self.layer, self.order = layer, order
        self.visible = True
        self.anim: Optional[str] = None
        self.frame = 0
        self.then: Optional[str] = None
        self.done = True
        self.contents = [(c["name"], int(c.get("count", "1") or 1)) for c in (d.contents if d else [])]
        self.base_anim = self._pick_base(initial_anim)
        self.set_anim(self.base_anim)
        self._offsets = world.offsets(self.cls)

    # ---- animations ---------------------------------------------------------
    @property
    def animations(self):
        ao = self.world.level.anims.get(self.cls)
        return ao.animations if ao else {}

    def _pick_base(self, wanted: Optional[str]) -> Optional[str]:
        anims = self.animations
        order = [wanted, "ms", "ms2", "ms0", "ms3", "ms1"] if self.kind == "actor" else [wanted, "ms"]
        for n in order:
            if n and n in anims:
                return n                         # may have 0 frames: object is then invisible
        for n, a in anims.items():
            if a.frames:
                return n
        return None

    def anim_names(self) -> List[str]:
        return list(self.animations)

    def set_anim(self, name: Optional[str], then: Optional[str] = None):
        self.anim, self.frame, self.then, self.done = name, 0, then, False
        self._emit_sfx()

    def play(self, name: str):
        """Play an animation once (or looped, as defined) and return to the base animation."""
        self.set_anim(name, then=self.base_anim if self.animations.get(name) and
                      self.animations[name].type != "loop" else None)

    def current_frame(self):
        a = self.animations.get(self.anim) if self.anim else None
        if a and a.frames:
            return a.frames[min(self.frame, len(a.frames) - 1)]
        return None

    def _emit_sfx(self):
        f = self.current_frame()
        if f is not None and f.sfx:
            self.world.sounds.append((f.sfx, self.room))

    def tick(self):
        a = self.animations.get(self.anim) if self.anim else None
        if not a or not a.frames:
            self.done = True
            if self.then:
                n, self.then = self.then, None
                self.set_anim(n)
            return
        if self.frame + 1 < len(a.frames):
            self.frame += 1
            self._emit_sfx()
        elif a.type == "loop":
            self.frame = 0
            self._emit_sfx()
        else:
            self.done = True
            if self.then:
                n, self.then = self.then, None
                self.set_anim(n)

    # ---- geometry -------------------------------------------------------------
    def sprite(self):
        """-> (image name, class folder, (x, y) top-left in level coordinates) or None."""
        f = self.current_frame()
        if f is None or f.gfx is None or not self.visible:
            return None
        off = self._offsets.get(f.gfx.lower(), (0, 0))
        x = self.origin[0] + off[0]
        y = self.origin[1] + off[1]
        if self.kind == "actor":
            hs = self.objdef.hotspot if self.objdef and self.objdef.hotspot else (0, 0)
            x += self.x - hs[0]
            y += self.y - hs[1]
        else:
            x += self.x
            y += self.y
        return f.gfx, self.cls, (x, y)

    def rect(self):
        s = self.sprite()
        if not s or self.world.bank is None:
            return None
        img = self.world.bank.get(s[0], s[1])
        return None if img is None else (s[2][0], s[2][1], img.width, img.height)

    def region_rect(self):
        """Click area from the <region> in anims.xml (objects painted into the background have no sprite)."""
        ao = self.world.level.anims.get(self.cls)
        if ao is None or self.kind == "actor":
            return None
        for r in ao.regions:
            if r.type is None and r.size[0] > 0 and r.size[1] > 0:
                return (self.origin[0] + self.x + r.position[0], self.origin[1] + self.y + r.position[1],
                        r.size[0], r.size[1])
        return None

    def label(self) -> str:
        txt = self.world.level.text(self.name)
        return txt or self.name


class Actor(Entity):
    """Woody (or any actor): walks, goes through doors.  Runs one script at a time."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.facing = 2                     # 0 back, 1 right, 2 front, 3 left
        self.gait = "mg"                    # "mg" walk | "mr" run (the angry neighbour)
        self.script: Optional[Iterator[None]] = None

    # speeds from objects.xml: <speed name="mg1" speed="17" .../> (pixels per animation step)
    def speed_of(self, direction: int) -> float:
        sp = self.objdef.speeds.get(f"{self.gait}{direction}") if self.objdef else None
        return float(sp["speed"]) if sp else 6.0

    def can_walk(self) -> bool:
        return "mg1" in self.animations

    def tick(self):
        super().tick()
        if self.script is not None:
            try:
                next(self.script)
            except StopIteration:
                self.script = None

    @property
    def busy(self) -> bool:
        return self.script is not None

    def stand(self, facing: Optional[int] = None):
        if facing is not None:
            self.facing = facing
        name = f"ms{self.facing}"
        if name in self.animations:
            self.base_anim = name
            self.set_anim(name)

    # ---- scripts -----------------------------------------------------------------
    locked = False                          # True while passing a door (cannot be interrupted)

    def walk_to(self, x: float, y: float):
        self.go_to(self.room, x, y)

    def go_to(self, room: str, x: float, y: float):
        """Walk to (x, y) of `room`; goes through the doors in between if it is another room."""
        if self.can_walk() and not self.locked:
            self.script = self._go(room, x, y)

    def use_door(self, door: Entity):
        """Walk to a door (in whatever room) and go through it."""
        if self.can_walk() and not self.locked:
            self.script = self._use(door)

    go_through = use_door

    def _hops(self, dest_room: str):
        for door in self.world.room_path(self.room, dest_room):
            yield from self._through(door)

    def _go(self, room: str, x: float, y: float):
        yield from self._unhide()
        yield from self._hops(room)
        if self.room == room:
            yield from self._walk(x, y)

    def _use(self, door: Entity):
        yield from self._unhide()
        yield from self._hops(door.room)
        if self.room == door.room:
            yield from self._through(door)

    def _walk(self, tx: float, ty: float):
        room = self.world.rooms[self.room]
        tx, ty = self.world.clamp_to_floor(room, tx, ty)
        cur = None
        while abs(tx - self.x) > 0.5 or abs(ty - self.y) > 0.5:
            dx, dy = tx - self.x, ty - self.y
            d = (1 if dx > 0 else 3) if abs(dx) >= abs(dy) else (2 if dy > 0 else 0)
            if d != cur:
                cur, self.facing = d, d
                self.base_anim = f"ms{d}" if f"ms{d}" in self.animations else self.base_anim
                self.set_anim(f"{self.gait}{d}" if f"{self.gait}{d}" in self.animations else f"mg{d}")
            step = self.speed_of(d)
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= step:
                self.x, self.y = tx, ty
            else:
                self.x += dx / dist * step
                self.y += dy / dist * step
            yield
        self.stand(cur if cur is not None else self.facing)

    def _door_action(self, door: Entity, action: str) -> dict:
        for a in (door.objdef.actions if door.objdef else []):
            if a.get("name") == action and a.get("actor") == self.name:
                return a
        return {}

    def _through(self, door: Entity):
        """Walk to the door, vanish into it (the door's own animation shows the character
        walking in), reappear at the partner door (whose animation shows him walking out)."""
        w = self.world
        self.locked = True
        yield from self._walk(*w.door_stand(door, self.name, ""))
        partner = w.partner_door(door)
        if partner is None:
            self.locked = False
            return
        self.visible = False                                    # the door animation draws him now
        door.set_anim(f"{self.name}_enter", then=door.base_anim)
        while door.anim != door.base_anim:
            yield
        dest = w.rooms[partner.room]
        self.room, self.origin = dest.name, dest.offset
        self.x, self.y = w.door_stand(partner, self.name, "_out")
        face = self._door_action(partner, "leave").get("actornextanim", "ms2")
        self.facing = int(face[-1]) if face[-1:].isdigit() else 2
        partner.set_anim(f"{self.name}_leave", then=partner.base_anim)
        while partner.anim != partner.base_anim:                # still invisible while walking out
            yield
        self.visible = True
        self.stand(self.facing)
        self.locked = False



    # ---- interacting with objects ---------------------------------------------------------
    hiding: Optional[Entity] = None

    def _own_actions(self, obj: Entity) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for a in (obj.objdef.actions if obj.objdef else []):
            if a.get("actor", self.name) == self.name:
                out.setdefault(a["name"], a)
        return out

    def _run_action(self, obj: Optional[Entity], act: dict, mid=None):
        """Play one <action> of objects.xml: actor animation + object animation, wait, call `mid`
        (the moment the effect happens), then the actor's follow-up animation."""
        aa, oa = act.get("actoranim"), act.get("objanim")
        n = 0
        if aa == "inv":
            self.visible = False
        elif aa in self.animations:
            self.set_anim(aa)
            n = len(self.animations[aa].frames)
        if obj is not None and oa and oa in obj.animations:
            obj.set_anim(oa, then=act.get("objnextanim") or None)
            n = max(n, len(obj.animations[oa].frames))
        t = act.get("time", "auto")
        for _ in range(n if t == "auto" or not str(t).isdigit() else int(t)):
            yield
        if mid:
            mid()
        if aa == "inv":
            self.visible = True
        nxt = act.get("actornextanim")
        if nxt in self.animations:
            if nxt.startswith("ms"):
                self.stand(int(nxt[-1]))
            else:
                self.set_anim(nxt)
                for _ in range(len(self.animations[nxt].frames)):
                    yield
                self.stand(self.facing)
        else:
            self.stand(self.facing)

    def _unhide(self):
        if self.hiding is not None:
            obj, self.hiding = self.hiding, None
            obj.set_anim("woody_leave", then=obj.base_anim)
            while obj.anim != obj.base_anim:
                yield
            self.visible = True
            self.stand(2)

    def _stand_at(self, obj: Entity):
        if obj.objdef and (obj.objdef.hotspots.get(self.name) or obj.objdef.hotspots.get("woody")):
            yield from self._walk(*self.world.stand_point(obj, self.name))

    def interact(self, obj: Entity):
        """Do the object's standard action: look / open / examine / hide."""
        if self.can_walk() and not self.locked:
            self.script = self._interact(obj)

    def _interact(self, obj: Entity):
        w = self.world
        yield from self._hops(obj.room)
        if self.room != obj.room:
            return
        yield from self._unhide()
        d = obj.objdef
        std = d.stdactions[0] if d and d.stdactions else "look"
        flags = d.flags if d else []
        acts = self._own_actions(obj)
        yield from self._stand_at(obj)
        if std == "hide" and "hideout" in flags:
            self.visible = False
            obj.set_anim("woody_enter", then=obj.base_anim)
            while obj.anim != obj.base_anim:
                yield
            self.hiding = obj
            self.facing = 2
        elif "container" in flags and std in ("open", "examine"):
            opened = False
            op = acts.get("open")
            if op and (op.get("actoranim") or op.get("objanim")):
                yield from self._run_action(obj, op)
                opened = True
            if obj.contents:
                if "take" in acts:
                    yield from self._run_action(obj, acts["take"], mid=lambda: w.take_contents(obj))
                else:
                    w.take_contents(obj)
            else:
                w.say("There is nothing else in here.")
            cl = acts.get("close")
            if opened and cl:
                yield from self._run_action(obj, cl)
        elif std == "use" and "singleuse" in flags:
            combo = w.find_single(obj.name)
            act = acts.get("use") or {"actoranim": "use_mid", "actornextanim": "smile", "time": "auto"}
            yield from self._run_action(
                obj, act, mid=(lambda: w.apply_combination(combo, "", obj.room)) if combo else None)
        else:                                                   # look at it
            self.stand(0)
            txt = w.level.text(obj.name, "description")
            if txt:
                w.say(txt)
            for _ in range(8):
                yield

    def use_item(self, item: str, target: Optional[Entity], room: str, x: float = 0, y: float = 0):
        """Use an inventory item on an object (`target`) or on the floor of `room`."""
        if self.can_walk() and not self.locked:
            self.script = self._use_item(item, target, room, x, y)

    def _use_item(self, item: str, target: Optional[Entity], room: str, x: float, y: float):
        w = self.world
        room = target.room if target else room
        yield from self._hops(room)
        if self.room != room:
            return
        yield from self._unhide()
        if target is not None:
            yield from self._stand_at(target)
        else:
            yield from self._walk(x, y)
        tname = target.name if target is not None else room
        combo = w.find_combination(item, tname)
        if combo is None and target is None:
            single = w.find_single(None, item)
            if single is not None:                              # e.g. the dog whistle
                owner = w.entity_named(single.name)
                act = self._own_actions(owner).get(item) if owner is not None else None
                yield from self._run_action(None, act or {"actoranim": "use_mid", "actornextanim": "smile"},
                                            mid=lambda: w.apply_combination(single, item, room))
                return
        if combo is None or combo.wrong:
            msg = w.level.text(combo.name, "wrong") if combo else None
            w.say(msg or "That doesn't work.")
            yield from self._run_action(None, {"actoranim": "decline"})
            return
        act = self._own_actions(target).get(item) if target is not None else None
        if act is None:
            act = {"actoranim": "use_low", "actornextanim": "smile", "time": "auto"}
        yield from self._run_action(target, act, mid=lambda: w.apply_combination(combo, item, room))


class World:
    def __init__(self, level: LevelData, bank: Optional[SpriteBank] = None):
        self.level = level
        self.bank = bank
        self.sounds: List[Tuple[str, Optional[str]]] = []      # (sound file, room) events
        self.rooms: Dict[str, Room] = dict(level.layout.rooms) if level.layout else {}
        self.entities: List[Entity] = []
        self.paused = False
        self.inventory: List[str] = []              # item names the player carries
        self.tricks: List[str] = []                 # combinations ("tricks") prepared so far
        self.message: Optional[list] = None         # [text, seconds left] shown as Woody's comment
        self.view_hint: Optional[str] = None
        self.score = 0                              # points of the tricks the neighbour fell for
        self.caught = False                         # the neighbour caught Woody -> restart the level
        self._acc = 0.0
        self._off_cache: Dict[str, Dict[str, Tuple[int, int]]] = {}
        order = 0
        lay = level.layout
        if lay:
            for p in lay.placements:
                self.entities.append(self._make(p, None, (0, 0), order)); order += 1
            for room in lay.rooms.values():
                for p in room.placements:
                    self.entities.append(self._make(p, room.name, room.offset, order)); order += 1
        self.woody: Optional[Actor] = next((e for e in self.entities
                                            if isinstance(e, Actor) and e.name == "woody"), None)
        self.ai = NeighborAI(self)

    # ---- construction ----------------------------------------------------------------
    def offsets(self, cls: str) -> Dict[str, Tuple[int, int]]:
        if cls not in self._off_cache:
            self._off_cache[cls] = {g.image.lower(): g.offset for g in self.level.gfx.get(cls, [])}
        return self._off_cache[cls]

    def _make(self, p: Placement, room: Optional[str], origin, order) -> Entity:
        pos = p.position or (0, 0)
        cls = Actor if p.kind == "actor" else Entity
        key = f"{room or '-'}:{p.kind}:{p.name}"
        e = cls(self, key, p.kind, p.name, room, origin, pos, p.layer, order, p.animation)
        e.visible = p.visible
        return e

    # ---- queries -----------------------------------------------------------------------
    def room_rect(self, name: str, margin: int = 8) -> Tuple[int, int, int, int]:
        """Level-coordinate rectangle that shows a room (union of its floor rectangles)."""
        r = self.rooms[name]
        xs0 = [r.offset[0] + f.offset[0] for f in r.floors]
        ys0 = [r.offset[1] + f.offset[1] for f in r.floors]
        xs1 = [r.offset[0] + f.offset[0] + f.size[0] for f in r.floors]
        ys1 = [r.offset[1] + f.offset[1] + f.size[1] for f in r.floors]
        x0, y0, x1, y1 = min(xs0) - margin, min(ys0) - margin, max(xs1) + margin, max(ys1) + margin
        return x0, y0, x1 - x0, y1 - y0

    def clamp_to_floor(self, room: Room, x: float, y: float) -> Tuple[float, float]:
        walk = [f for f in room.floors if not f.wall] or room.floors
        ymin = min(f.offset[1] for f in walk)
        ymax = room.path1[1]
        xmin, xmax = sorted((room.path1[0], room.path2[0]))
        return min(max(x, xmin), xmax), min(max(y, ymin), max(ymin, ymax))

    def door(self, name: str) -> Optional[Entity]:
        return next((e for e in self.entities if e.kind == "door" and e.name == name), None)

    def door_stand(self, door: Entity, actor: str, suffix: str) -> Tuple[float, float]:
        """Where an actor stands to use a door (door position + hotspot from objects.xml)."""
        hs = door.objdef.hotspots if door.objdef else {}
        h = hs.get(actor + suffix) or hs.get(actor) or hs.get("woody" + suffix) or (0, 0)
        return door.x + h[0], door.y + h[1]

    def partner_door(self, door: Entity) -> Optional[Entity]:
        room = self.rooms.get(door.room)
        if room:
            for n in room.neighbors:
                if n.doorin == door.name:
                    return self.door(n.doorout)
        return None

    def room_path(self, src: str, dst: str) -> List[Entity]:
        """Doors to pass (in order) to get from room `src` to room `dst` (cheapest route)."""
        import heapq
        if src == dst or src not in self.rooms or dst not in self.rooms:
            return []
        best = {src: 0}
        prev: Dict[str, Tuple[str, Entity]] = {}
        heap = [(0, src)]
        while heap:
            cost, r = heapq.heappop(heap)
            if r == dst:
                break
            if cost > best.get(r, 1e18):
                continue
            for n in self.rooms[r].neighbors:
                door = self.door(n.doorin)
                if door is None or n.name not in self.rooms:
                    continue
                c = cost + max(1, n.costs)
                if c < best.get(n.name, 1e18):
                    best[n.name] = c
                    prev[n.name] = (r, door)
                    heapq.heappush(heap, (c, n.name))
        path, r = [], dst
        while r in prev:
            r, door = prev[r][0], prev[r][1]
            path.append(door)
        return path[::-1] if dst in prev else []

    def room_at(self, x: float, y: float) -> Optional[str]:
        """Name of the room whose area contains the level point (x, y), or None."""
        found, area = None, 1e18
        for name in self.rooms:
            rx, ry, rw, rh = self.room_rect(name, 0)
            if rx <= x < rx + rw and ry <= y < ry + rh and rw * rh < area:
                found, area = name, rw * rh
        return found

    def entities_in(self, room: Optional[str]) -> List[Entity]:
        return [e for e in self.entities if e.room == room]

    def draw_order(self) -> List[Entity]:
        return sorted(self.entities, key=lambda e: (e.layer, e.order))

    def hit_test(self, x: float, y: float, room: Optional[str] = None) -> Optional[Entity]:
        """Topmost entity under the level point (x, y): a visible sprite pixel, or an object's click region."""
        for e in sorted(self.entities, key=lambda e: (e.layer, e.order), reverse=True):
            if e.room is None or (room is not None and e.room != room) or not e.visible:
                continue
            if e.kind == "object" and e.objdef is None:
                continue
            r = e.rect()
            if r and r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]:
                s = e.sprite()
                img = self.bank.get(s[0], s[1])
                if img is not None and img.getpixel((int(x - r[0]), int(y - r[1])))[3] > 32:
                    return e
            if e.kind == "object":
                g = e.region_rect()
                if g and g[0] <= x < g[0] + g[2] and g[1] <= y < g[1] + g[3]:
                    return e
        return None

    # ---- inventory and combinations -----------------------------------------------------------
    def say(self, text: str, seconds: float = 4.0):
        self.message = [text, seconds]

    def item_name(self, item: str) -> str:
        return self.level.text(item) or item

    def stand_point(self, obj: Entity, actor: str) -> Tuple[float, float]:
        hs = obj.objdef.hotspots if obj.objdef else {}
        h = hs.get(actor) or hs.get("woody") or (0, 0)
        return obj.x + h[0], obj.y + h[1]

    def take_contents(self, obj: Entity):
        for name, count in obj.contents:
            self.inventory.extend([name] * count)
            self.say(self.level.text(name, "description") or self.item_name(name))
        obj.contents = []

    def find_combination(self, item: str, target: str):
        want = {item, target}
        for c in self.level.combinations:
            if {i[0] for i in c.ingredients} == want:
                return c
        return None

    def find_single(self, obj_name: Optional[str], item: Optional[str] = None):
        """One-ingredient combination: an object used on its own, or an item used on its own."""
        want = obj_name if obj_name is not None else item
        for c in self.level.combinations:
            if len(c.ingredients) == 1 and c.ingredients[0][0] == want:
                return c
        return None

    def entity_named(self, name: str, room: Optional[str] = None) -> Optional[Entity]:
        for e in self.entities:
            if e.name == name and e.visible and (room is None or e.room == room):
                return e
        return None

    def spawn(self, name: str, room: str, layer: int, anim: Optional[str] = None) -> Entity:
        r = self.rooms[room]
        e = Entity(self, f"{room}:object:{name}", "object", name, room, r.offset, (0, 0),
                   layer, len(self.entities), anim)
        self.entities.append(e)
        return e

    def apply_combination(self, combo, item: str, room: str):
        """The trick happens: items/objects are consumed, the result object appears."""
        layer = combo.layer
        for ing, remove in combo.ingredients:
            if not remove:
                continue
            if ing == item and item in self.inventory:
                self.inventory.remove(item)
                continue
            e = self.entity_named(ing)
            if e is not None:
                e.visible = False
                layer = layer if layer is not None else e.layer
        res_room = combo.name.split("/")[0] if combo.name.split("/")[0] in self.rooms else room
        if combo.name in self.level.objects and self.entity_named(combo.name) is None:
            old = next((e for e in self.entities if e.name == combo.name and e.kind == "object"), None)
            if old is not None:                                 # toggles (valve on/off): show it again
                old.visible = True
                old.set_anim(combo.animation or old.base_anim)
            else:
                self.spawn(combo.name, res_room, layer if layer is not None else 3, combo.animation)
        if combo.trick and combo.name not in self.tricks:
            self.tricks.append(combo.name)
        self.ai.noise(room, 1)
        txt = self.level.text(combo.name, "description")
        if txt:
            self.say(txt)

    # ---- time -----------------------------------------------------------------------------
    def update(self, dt: float):
        if self.paused:
            return
        if self.message:
            self.message[1] -= dt
            if self.message[1] <= 0:
                self.message = None
        self._acc += dt * ANIM_FPS
        while self._acc >= 1.0:
            self._acc -= 1.0
            for e in self.entities:
                e.tick()
        self.ai.update(dt)

    def drain_sounds(self) -> List[Tuple[str, Optional[str]]]:
        s, self.sounds = self.sounds, []
        return s


def new_world(level_name: str, bank: Optional[SpriteBank] = None) -> World:
    return World(load_level(level_name), bank)
