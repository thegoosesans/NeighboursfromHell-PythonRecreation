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

import random

from .model import LevelData, Placement, Room, level_sets, load_level
from .assets import SpriteBank

ANIM_FPS = 12.0                 # animation steps per second (assumption: the XML does not say)
DOOR_NEXT_AFTER_FRAMES = 0      # the next door's animation starts once this many frames of the first
                                # door's animation have played (shorter ones: when it has ended)
SMILE_IDLE_SECONDS = 2.5        # how long the smile's last frame (w_smile_0012) stays Woody's idle pose


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
        self._hold = 0                          # > 0: frozen on the current frame for that many ticks
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
        self._hold = 0                                    # any new animation cancels a frame hold
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
        if self._hold > 0:                              # frozen on a frame (e.g. the smile idle)
            self._hold -= 1
            if self._hold == 0:
                self.set_anim(self.base_anim)           # back to the normal standing animation
            return
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
        self.script: Optional[Iterator[None]] = None
        self.locked = False                 # True while passing a door or entering/leaving a hideout
        self.hiding: Optional[Entity] = None
        self.progress: Optional[float] = None   # 0..1 while a trick action runs, else None
        self.sneak = False                      # Woody sneaks ("sn" gait: slow, no noise)
        self.running = False                    # the neighbour runs ("mr" gait) when angry
        self.panicked = False                   # Woody froze after being spotted / waking a pet

    # speeds from objects.xml: <speed name="mg1" speed="17" .../> (pixels per animation step)
    @property
    def gait(self) -> str:
        """Animation / speed prefix in use: "mr" run, "sn" sneak or "mg" normal walk."""
        p = "mr" if self.running else ("sn" if self.sneak else "mg")
        return p if f"{p}1" in self.animations else "mg"

    def speed_of(self, direction: int) -> float:
        sp = self.objdef.speeds.get(f"{self.gait}{direction}") if self.objdef else None
        return float(sp["speed"]) if sp else 6.0

    def noise_of(self, direction: int) -> int:
        """Noise level of a step (objects.xml: <speed ... noise="1"/>): 1 = footsteps, sneaking 0."""
        sp = self.objdef.speeds.get(f"{self.gait}{direction}") if self.objdef else None
        return int(sp.get("noise", "0") or 0) if sp else 0

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
    def _start(self, script: Iterator[None]) -> bool:
        """Replace the current script with a new one: the action Woody was doing is
        cancelled (the player clicked somewhere else / changed his mind)."""
        if self.script is not None:
            close = getattr(self.script, "close", None)
            if close is not None:
                close()
        self.script = script
        return True

    def panic_from(self, threat: Entity):
        """Freeze Woody in fear; the neighbour must catch/reset him to unlock him.

        Call this through Actor._start(), which has already cancelled any previous
        script before installing this generator.
        """
        self.progress = None
        self.visible = True
        self.locked = True
        self.panicked = True

        name = "fear1" if self.x < threat.x else "fear3"
        if name in self.animations:
            self.set_anim(name)
            for _ in range(len(self.animations[name].frames)):
                yield
        else:
            # A converted level may not contain this animation; keep him frozen.
            for _ in range(12):
                yield

    def walk_to(self, x: float, y: float) -> bool:
        return self.go_to(self.room, x, y)

    def go_to(self, room: str, x: float, y: float) -> bool:
        """Walk to (x, y) of `room`; goes through the doors in between if it is another room."""
        if self.can_walk() and not self.locked:
            return self._start(self._go(room, x, y))
        return False

    def use_door(self, door: Entity) -> bool:
        """Walk to a door and go through it, unless either door in the pair is reserved."""
        if (self.can_walk()
                and not self.locked
                and self.world.claim_door(door, self)):
            return self._start(self._use_door_claimed(door))
        return False

    go_through = use_door

    def _use_door_claimed(self, door: Entity):
        try:
            yield from self._use(door)
        finally:
            self.world.release_door(door, self)

    def _hops(self, dest_room: str):
        for door in self.world.room_path(self.room, dest_room):
            # Reserve both sides before walking to the door so the other actor cannot
            # begin an overlapping enter/leave interaction.
            if not self.world.claim_door(door, self):
                return
            try:
                yield from self._through(door)
            finally:
                self.world.release_door(door, self)

    def _go(self, room: str, x: float, y: float):
        yield from self._unhide()
        yield from self._hops(room)
        if self.room == room:
            # just walking around: Woody stays on the room's walking line (path1->path2),
            # so only his x position changes - if an interaction (or stepping out of a
            # door) left him standing off the line, the "U"-shaped _walk first brings
            # him back onto it, then he walks on in x
            r = self.world.rooms[room]
            yield from self._walk(x, self.world.path_y(r, x))

    def _use(self, door: Entity):
        yield from self._unhide()
        yield from self._hops(door.room)
        if self.room == door.room:
            yield from self._through(door)

    def _walk(self, tx: float, ty: float, order: str = "xy"):
        """Walk to (tx, ty) of the current room in the shape of a flipped cyrillic "П"
        (i.e. a normal letter "U"): first vertically back onto the room's walking line
        (path1->path2), then horizontally ALONG THE LINE, then vertically to the target's
        depth. Woody never walks diagonally, and because the horizontal leg always happens
        on the walking line he no longer crosses furniture that stands between him and
        the target. (`order` - "xy"/"yx" from the old straight-line approach - is kept
        for the callers; both orders now route over the walking line.)"""
        room = self.world.rooms[self.room]
        tx, ty = self.world.clamp_to_floor(room, tx, ty)
        yield from self._walk_y(self.world.path_y(room, self.x))    # back onto the line
        yield from self._walk_x(tx, follow_line=True)               # along the line
        yield from self._walk_y(ty)                                 # to the target's depth
        self.stand(self.facing)

    def _gait(self, d: int):
        """Face direction d and switch to its walk animation (kept while it continues)."""
        g = f"{self.gait}{d}"
        if self.anim != g:
            self.facing = d
            if f"ms{d}" in self.animations:
                self.base_anim = f"ms{d}"
            if g in self.animations:
                self.set_anim(g)

    def _walk_x(self, tx: float, follow_line: bool = False):
        d = 1 if tx > self.x else 3
        room = self.world.rooms[self.room]
        while abs(tx - self.x) > 0.5:
            self._gait(d)
            step = self.speed_of(d)
            self.x = tx if abs(tx - self.x) <= step else self.x + (step if d == 1 else -step)
            if follow_line:                         # keep the depth on the walking line
                self.y = self.world.path_y(room, self.x)
            self.world.make_noise(self.room, self.noise_of(d))
            yield

    def _walk_y(self, ty: float):
        d = 2 if ty > self.y else 0
        while abs(ty - self.y) > 0.5:
            self._gait(d)
            step = self.speed_of(d)
            self.y = ty if abs(ty - self.y) <= step else self.y + (step if d == 2 else -step)
            self.world.make_noise(self.room, self.noise_of(d))
            yield

    def _door_action(self, door: Entity, action: str) -> dict:
        for a in (door.objdef.actions if door.objdef else []):
            if a.get("name") == action and a.get("actor") == self.name:
                return a
        return {}

    def _exit_door(self, partner: Entity):
        """Move to the partner door's room and start its walking-out animation."""
        w = self.world
        dest = w.rooms[partner.room]
        self.room, self.origin = dest.name, dest.offset
        self.x, self.y = w.door_stand(partner, self.name, "_out")
        face = self._door_action(partner, "leave").get("actornextanim", "ms2")
        self.facing = int(face[-1]) if face[-1:].isdigit() else 2
        partner.set_anim(f"{self.name}_leave", then=partner.base_anim)

    def _through(self, door: Entity):
        """Walk to the door, vanish into it (the door's own animation shows the character
        walking in), reappear at the partner door (whose animation shows him walking out).
        The partner door's animation starts after DOOR_NEXT_AFTER_FRAMES (0 = at once) frames of the
        enter animation have played - or right when it ends if it is shorter than that - so the
        two doors play seamlessly."""
        w = self.world
        yield from self._walk(*w.door_stand(door, self.name, ""))
        partner = w.partner_door(door)
        if partner is None:
            return
        self.locked = True                              # the door sequence itself cannot be interrupted
        self.visible = False                                    # the door animation draws him now
        door.set_anim(f"{self.name}_enter", then=door.base_anim)
        entered = door.animations.get(f"{self.name}_enter")
        total = len(entered.frames) if entered else 0
        out = False
        while door.anim != door.base_anim:
            if not out and door.frame >= DOOR_NEXT_AFTER_FRAMES:  # enough frames of this door have played
                out = True
                self._exit_door(partner)
            yield
        if not out:
            self._exit_door(partner)
        while partner.anim != partner.base_anim:                # still invisible while walking out
            yield
        self.visible = True
        self.stand(self.facing)
        self.locked = False                     # the door's out-point may be off the walking
                                                # line - the next "U"-shaped walk returns onto it



    # ---- interacting with objects ---------------------------------------------------------
    def _own_actions(self, obj: Entity) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for a in (obj.objdef.actions if obj.objdef else []):
            if a.get("actor", self.name) == self.name:
                out.setdefault(a["name"], a)
        return out

    def _run_action(self, obj: Optional[Entity], act: dict, mid=None, bar: bool = False):
        """Play one <action> of objects.xml: actor animation + object animation, wait, call `mid`
        (the moment the effect happens), then the actor's follow-up animation.
        `bar`: while waiting, track the action's progress (self.progress, 0..1) so the
        viewer can show the trick progress bar above Woody's head."""
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
        steps = n if t == "auto" or not str(t).isdigit() else int(t)
        if bar and steps > 0:
            self.progress = 0.0
        completed = False
        try:
            for i in range(steps):
                if self.progress is not None:
                    self.progress = (i + 1) / steps   # the bar fills as the action advances
                yield
            if mid:
                mid()
            completed = True
        finally:
            self.progress = None                    # also when the action is cancelled
            if aa == "inv":
                self.visible = True
            if (not completed and obj is not None and oa
                    and obj.anim == oa):
                # Do not leave a combined actor/object trick animation running after cancel.
                obj.set_anim(obj.base_anim)
        nxt = act.get("actornextanim")
        emo = aa if aa in ("smile", "decline") else None     # the action itself was smile / nono
        if nxt in self.animations:
            if nxt.startswith("ms"):
                self.stand(int(nxt[-1]))
            else:
                self.set_anim(nxt)
                for _ in range(len(self.animations[nxt].frames)):
                    yield
                if nxt in ("smile", "decline"):
                    self.hold_smile_idle()                   # keep w_smile_0012 for a while
                else:
                    self.stand(self.facing)
        elif emo:
            self.hold_smile_idle()
        else:
            self.stand(self.facing)

    def _smile(self):
        """Woody laughs at the camera after a successful trick, then keeps smiling for a while."""
        if "smile" in self.animations:
            self.set_anim("smile")
            for _ in range(len(self.animations["smile"].frames)):
                yield
        self.hold_smile_idle()

    def _whatsup(self):
        """Empty container: Woody shrugs at the camera - the "whatsup" animation
        (called 'lose' in the data files, its frames are w_whatsup_*)."""
        name = next((n for n in ("whatsup", "lose") if n in self.animations), None)
        if name is None:
            return
        self.set_anim(name)
        for _ in range(len(self.animations[name].frames)):
            yield
        self.stand(self.facing)

    def hold_smile_idle(self, seconds: float = SMILE_IDLE_SECONDS):
        """After 'smile' / 'nono': Woody does not switch back to the normal standing
        animation ('ms...') at once but stands still on the smile's last frame
        (w_smile_0012) for `seconds`; any new action cancels the pose."""
        a = self.animations.get("smile")
        if not a or not a.frames:
            self.stand(self.facing)
            return
        idx = len(a.frames) - 1                              # the smile's last frame ...
        for i, f in enumerate(a.frames):                     # ... is w_smile_0012 in the data
            if f.gfx and "smile_0012" in f.gfx.lower():
                idx = i
        self.anim, self.frame, self.then, self.done = "smile", idx, None, True
        self._hold = max(1, int(seconds * ANIM_FPS))

    def _unhide(self):
        if self.hiding is not None:
            self.locked = True                          # leaving cannot be interrupted either
            obj, self.hiding = self.hiding, None
            obj.set_anim("woody_leave", then=obj.base_anim)
            while obj.anim != obj.base_anim:
                yield
            self.visible = True
            self.stand(2)
            self.locked = False

    def _stand_at(self, obj: Entity):
        if obj.objdef and (obj.objdef.hotspots.get(self.name) or obj.objdef.hotspots.get("woody")):
            yield from self._walk(*self.world.stand_point(obj, self.name))

    def interact(self, obj: Entity) -> bool:
        """Do the object's standard action: look / open / examine / hide."""
        if self.can_walk() and not self.locked:
            return self._start(self._interact(obj))
        return False

    def _interact(self, obj: Entity):
        if self.hiding is obj:
            # Clicking the occupied hideout toggles it off exactly once.
            yield from self._unhide()
            return
        w = self.world
        yield from self._unhide()
        yield from self._hops(obj.room)
        if self.room != obj.room:
            return
        d = obj.objdef
        std = d.stdactions[0] if d and d.stdactions else "look"
        flags = d.flags if d else []
        acts = self._own_actions(obj)
        yield from self._stand_at(obj)
        if std == "hide" and "hideout" in flags:
            self.locked = True                          # entering a hideout ignores all input
            self.visible = False
            obj.set_anim("woody_enter", then=obj.base_anim)
            while obj.anim != obj.base_anim:
                yield
            self.hiding = obj
            self.facing = 2
            self.locked = False                       # hidden: he can leave at any time again
        elif "container" in flags and std in ("open", "examine"):
            # rummaging through a container: like hiding, mouse input does not count while
            # Woody opens it, takes what he finds ("found an object") or shrugs ("whatsup")
            self.locked = True
            try:
                opened = False
                op = acts.get("open")
                if op and (op.get("actoranim") or op.get("objanim")):
                    yield from self._run_action(obj, op)
                    opened = True
                if obj.contents:                                # Woody found something
                    if "take" in acts:
                        yield from self._run_action(obj, acts["take"], mid=lambda: w.take_contents(obj))
                    else:
                        w.take_contents(obj)
                else:                                           # nothing inside: the "whatsup" animation
                    w.say("There is nothing else in here.")
                    yield from self._whatsup()
                cl = acts.get("close")
                if opened and cl:
                    yield from self._run_action(obj, cl)
            finally:
                self.locked = False
        elif std == "use" and "singleuse" in flags:
            combo = w.find_single(obj.name)
            act = acts.get("use") or {"actoranim": "use_mid", "actornextanim": "smile", "time": "auto"}
            yield from self._run_action(
                obj, act, bar=bool(combo and combo.trick),
                mid=(lambda: w.apply_combination(combo, "", obj.room)) if combo else None)
            if combo and combo.trick and act.get("actornextanim") != "smile":
                yield from self._smile()
        else:                                                   # look at it
            self.stand(0)
            txt = w.level.text(obj.name, "description")
            if txt:
                w.say(txt)
            for _ in range(8):
                yield

    def use_item(self, item: str, target: Optional[Entity], room: str, x: float = 0, y: float = 0) -> bool:
        """Use an inventory item on an object (`target`) or on the floor of `room`."""
        if self.can_walk() and not self.locked:
            return self._start(self._use_item(item, target, room, x, y))
        return False

    def _use_item(self, item: str, target: Optional[Entity], room: str, x: float, y: float):
        w = self.world
        room = target.room if target else room
        yield from self._unhide()
        yield from self._hops(room)
        if self.room != room:
            return
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
                if act is None:
                    act = {"actoranim": "use_mid", "actornextanim": "smile"}
                yield from self._run_action(None, act, bar=bool(single.trick),
                                            mid=lambda: w.apply_combination(single, item, room))
                if single.trick and act.get("actornextanim") != "smile":
                    yield from self._smile()
                return
        if combo is None or combo.wrong:
            msg = w.level.text(combo.name, "wrong") if combo else None
            if msg:
                w.say(msg)                              # unique Woody line; otherwise he just gestures
            yield from self._run_action(None, {"actoranim": "decline"})     # the "nono" animation
            return
        act = self._own_actions(target).get(item) if target is not None else None
        if act is None and target is None:
            low = next((a for a in ("take_low", "take_low", "laydown") if a in self.animations),
                       "use_low")
            act = {"actoranim": low, "actornextanim": "smile", "time": "auto"}
        elif act is None:
            act = {"actoranim": "use_low", "actornextanim": "smile", "time": "auto"}
        yield from self._run_action(
            target, act, bar=target is not None and combo.trick,
            mid=lambda: w.apply_combination(combo, item, room,
                                            pos=(self.x, self.y) if target is None else None))
        if combo.trick and act.get("actornextanim") != "smile":
            yield from self._smile()


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
        self.ai = True                              # neighbour / pets act on their own
        self.rng = random.Random()
        self.tick_no = 0
        self.noise_log: List[Tuple[int, str, int]] = []   # (counter, room, level) - footsteps 1, barking 2
        self.noise_count = 0
        self.trick_counts: Dict[str, int] = {}      # how often each trick worked on the neighbour
        self.trick_epoch: Dict[str, int] = {}       # increases every time a trick is (re)armed
        self.trick_sources: Dict[str, List[Entity]] = {}   # objects a trick replaced (restored on undo)
        self.score = 0.0                            # TV rating earned (sum of trick quotas)
        self.lives = 3
        self.finished: Optional[str] = None         # None | "won" | "lost"
        info = next((li for ls in level_sets() for li in ls.levels if li.name == level.name), None)
        self.minquota = info.minquota if info else 0
        self.time_left = info.time if info and info.time else 0      # animation ticks; 0 = no limit
        self.toasts: List[str] = []                 # short messages like "+7 rating" for the viewer
        self._acc = 0.0
        self._off_cache: Dict[str, Dict[str, Tuple[int, int]]] = {}
        self.door_locks: Dict[str, Actor] = {}
        self.tricks_in_row = 0
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
        self.woody_start = (self.woody.room, (self.woody.x, self.woody.y)) if self.woody else None
        self.neighbor = next((e for e in self.entities if e.name == "neighbor"), None)
        self.neighbor_start = (self.neighbor.room, (self.neighbor.x, self.neighbor.y)) if self.neighbor else None

    # ---- construction ----------------------------------------------------------------
    def offsets(self, cls: str) -> Dict[str, Tuple[int, int]]:
        if cls not in self._off_cache:
            self._off_cache[cls] = {g.image.lower(): g.offset for g in self.level.gfx.get(cls, [])}
        return self._off_cache[cls]

    def _make(self, p: Placement, room: Optional[str], origin, order) -> Entity:
        pos = p.position or (0, 0)
        cls = Entity
        if p.kind == "actor":
            from .ai import Neighbor, Pet                      # the brains live in ai.py
            cls = Neighbor if p.name == "neighbor" else Pet if p.name in ("dog", "chili") else Actor
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

    def path_y(self, room: Room, x: float) -> float:
        """Y of the room's walking line (path1 -> path2) at x: Woody's normal depth while
        he just walks around. He leaves the line only to reach an object / drop an item
        and returns to it before walking on."""
        (x1, y1), (x2, y2) = room.path1, room.path2
        if x1 == x2:
            return y1
        t = min(max((x - x1) / (x2 - x1), 0.0), 1.0)
        return y1 + t * (y2 - y1)

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

    def _door_pair(self, door: Entity) -> Tuple[Entity, ...]:
        partner = self.partner_door(door)
        return (door, partner) if partner is not None else (door,)

    def claim_door(self, door: Entity, actor: Actor) -> bool:
        """Reserve a door and its partner for one actor."""
        pair = self._door_pair(door)
        if any(self.door_locks.get(e.key) not in (None, actor) for e in pair):
            return False
        for e in pair:
            self.door_locks[e.key] = actor
        return True

    def release_door(self, door: Entity, actor: Actor):
        for e in self._door_pair(door):
            if self.door_locks.get(e.key) is actor:
                del self.door_locks[e.key]

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

    def apply_combination(self, combo, item: str, room: str, pos: Optional[Tuple[float, float]] = None):
        """The trick happens: items/objects are consumed, the result object appears.
        `pos` is where a floor trick's result (marbles, banana peel, soap ...) is dropped -
        Woody's feet at the moment of use; object results keep their class offsets (0, 0)."""
        layer = combo.layer
        hidden: List[Entity] = []
        for ing, remove in combo.ingredients:
            if not remove:
                continue
            if ing == item and item in self.inventory:
                self.inventory.remove(item)
                continue
            e = self.entity_named(ing)
            if e is not None:
                e.visible = False
                hidden.append(e)
                layer = layer if layer is not None else e.layer
        if combo.trick:
            self.trick_sources[combo.name] = hidden
            self.trick_epoch[combo.name] = self.trick_epoch.get(combo.name, 0) + 1
        res_room = combo.name.split("/")[0] if combo.name.split("/")[0] in self.rooms else room
        if combo.name in self.level.objects and self.entity_named(combo.name) is None:
            old = next((e for e in self.entities if e.name == combo.name and e.kind == "object"), None)
            if old is not None:                                 # toggles (valve on/off): show it again
                old.visible = True
                if pos is not None:
                    old.x, old.y = pos
                old.set_anim(combo.animation or old.base_anim)
            else:
                e = self.spawn(combo.name, res_room, layer if layer is not None else 3, combo.animation)
                if pos is not None:
                    e.x, e.y = pos
        if combo.trick and combo.name not in self.tricks:
            self.tricks.append(combo.name)
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
            self.tick_no += 1
            if self.finished is None and self.time_left > 0:
                self.time_left -= 1
                if self.time_left == 0:
                    self.finish("won" if self.score >= self.minquota else "lost")
            if self.finished is None and not self.time_left and self.minquota and self.score >= self.minquota \
                    and self.level.name.startswith("tutorial"):
                self.finish("won")
            # Objects and doors tick first, actors last: when a door / hideout finishes the animation
            # that shows Woody, his own script (running in his tick) sees it in the same step and
            # makes him visible again at once - otherwise there would be one frame without him.
            for e in self.entities:
                if not isinstance(e, Actor):
                    e.tick()
            for e in self.entities:
                if isinstance(e, Actor):
                    e.tick()

    # ---- noise, scoring, game flow -----------------------------------------------------------
    def make_noise(self, room: Optional[str], level: int):
        """Footsteps (1) and barking (2): the neighbour and the pets listen to this."""
        if level > 0 and room is not None:
            self.noise_count += 1
            self.noise_log.append((self.noise_count, room, level))
            del self.noise_log[:-60]

    def trick_triggered(self, name: str) -> float:
        """The neighbour fell for a trick: rating += its quota (quota1..4 = 1st..4th time)."""
        t = self.level.tricks.get(name)
        n = self.trick_counts.get(name, 0)
        pts = float(t.quotas[min(n, 3)]) if t else 0.0
        self.trick_counts[name] = n + 1
        self.score += pts
        self.tricks_in_row += 1
        if pts:
            self.toasts.append(f"+{int(pts)}")
        return pts

    def angry_ticks(self, name: Optional[str] = None) -> int:
        t = self.level.tricks.get(name) if name else None
        return (t.angrytime if t and t.angrytime else 0) or self.level.layout.angrytime or 200

    def undo_trick(self, name: str):
        """Back to normal (the neighbour cleaned / repaired it): the result goes away, the objects
        it replaced come back, and the trick can be prepared again."""
        for e in self.entities:
            if e.name == name and e.kind == "object":
                e.visible = False
        for e in self.trick_sources.pop(name, []):
            e.visible = True
        if name in self.tricks:
            self.tricks.remove(name)

    def finish(self, how: str):
        if self.finished is None:
            self.finished = how

    def on_caught(self, by_neighbor: bool = False):
        """The neighbour got Woody: one life less, Woody starts again at the entrance."""
        wd = self.woody
        if wd is None:
            return
        self.lives -= 1
        if wd.script is not None:
            wd.script.close()
            wd.script = None
        # Recover any hideout animation left active by an interrupted transition.
        for e in self.entities:
            if e.kind == "object" and e.anim in ("woody_enter", "woody_leave"):
                e.set_anim(e.base_anim)

        wd.hiding = None
        wd.locked = False
        wd.visible = True
        wd.progress = None
        wd.panicked = False
        if self.woody_start and self.woody_start[0] in self.rooms:
            room, pos = self.woody_start
            r = self.rooms[room]
            wd.room, wd.origin, (wd.x, wd.y) = room, r.offset, pos
        wd.stand(2)
        nb = self.neighbor                                       # a fresh start for both of them
        if nb is not None and self.neighbor_start:
            if nb.script is not None and not by_neighbor:       # (his own script is running right now)
                nb.script.close()
                nb.script = None
            room, pos = self.neighbor_start
            r = self.rooms[room]
            nb.room, nb.origin, (nb.x, nb.y) = room, r.offset, pos
            nb.visible, nb.locked, nb.committed, nb.sleeping = True, False, False, None
            nb.mode, nb.angry_left, nb.running, nb._awake_ticks = "routine", 0, False, 0
            nb.stand(2)
        door = self.door("fro/anc")
        if door is not None and wd.room == "fro":
            wd.use_door(door)
        if self.lives <= 0:
            self.finish("lost")
            self.say("Caught again - that's the end of this episode!", 6)
        else:
            self.say(f"Caught! {self.lives} {'life' if self.lives == 1 else 'lives'} left.", 4)

    def recent_noise(self, after: int) -> List[Tuple[int, str, int]]:
        return [n for n in self.noise_log if n[0] > after]

    def drain_sounds(self) -> List[Tuple[str, Optional[str]]]:
        s, self.sounds = self.sounds, []
        return s


def new_world(level_name: str, bank: Optional[SpriteBank] = None) -> World:
    return World(load_level(level_name), bank)
