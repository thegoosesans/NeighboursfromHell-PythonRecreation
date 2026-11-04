"""The neighbour (and the pets): artificial intelligence.

The XML files describe *what the neighbour can do* - his animations, speeds (walk "mg", run "mr"),
the actions he has on every object (hotspot where he stands + animations), the noise triggers
("wakeup" at noise 1 in his room, "alarm" at noise 2 anywhere), "near object" triggers, and for
every trick its quotas and "angry" time.  They do not contain a script of his day, so the brain
below is built from those pieces:

  routine   he idles, uses objects that have neighbour actions (fridge, keyboard ...), walks around
            and sometimes goes to bed.  Tricks Woody prepared are used first - when the neighbour
            uses a trick object the trick works: reaction, rating += quota, he gets angry.
  sleeping  invisible in bed (the bed's own enter / sleep / leave animations); Woody's footsteps
            in the room (noise 1) or barking anywhere (noise 2) wake him; the alarm clock rings
            by itself after a while.
  angry     after a trick or a noise he runs from room to room looking for Woody (and checks
            wardrobes / beds).
  chase     he saw Woody (same room, not hidden): runs after him, follows through doors, and
            catches him - the "kill" animation, Woody loses a life and starts again.
  hidden    if he saw Woody hide, he walks to the hideout, searches and drags him out.

Things he walks past (banana peel, soap, marbles, skateboard) trigger via the XML's nearobj
triggers; objects such as the dirty microwave are cleaned / repaired by him afterwards, which
puts the object back so the trick can be prepared again.
"""
from __future__ import annotations
from typing import Dict, Optional

from .engine import Actor, Entity

# order in which the neighbour performs the actions of an object he uses
RANK = (("enter", 0), ("open", 10), ("search", 15), ("take", 20), ("sit", 30), ("play", 31),
        ("read", 31), ("make", 31), ("use", 32), ("put", 32), ("wash", 33), ("dry", 33),
        ("drink", 35), ("eat", 35), ("sniff", 35), ("sleep", 40), ("give", 60), ("close", 70),
        ("leave", 90))
SKIP = ("clean", "repair", "ring", "hit_")           # undo actions / internal ones
UNDO = ("clean", "repair")
OWN_REACTION = {"surprise", "fall", "hurt", "cry", "burn", "spit_fire", "shocked", "scared"}
SLIP = {"banana_on_floor", "soap_on_floor", "marbles", "skate"}   # behaviours that make him slip

# keywords of a trick's name -> how the neighbour reacts (his own animations from objects.xml)
REACTIONS = ((("nitro", "boom", "explos", "candle"), ("shake_bottle", "bottle_explode")),
             (("hot", "tabasco", "spic", "pepper", "fire", "vinegar", "chili"), ("spit_fire",)),
             (("glue", "sticky", "pins", "nail", "saw", "knot", "smash", "hole", "cut", "tong"),
              ("hurt",)),
             (("fart", "smear", "soil", "dirty", "stuffed", "flood"), ("surprise",)))


def rank(name: str) -> Optional[int]:
    if name.startswith(SKIP):
        return None
    for key, r in RANK:
        if name.startswith(key):
            return r
    return 50


class Neighbor(Actor):
    SIGHT_TICKS = 6          # he must see Woody this long before he reacts (half a second)
    CATCH_DX = 75            # close enough to grab him
    TRIGGER_DX = 110         # "near object" trigger distance along the walking line
    SLEEP_EVERY = 900        # awake ticks before he goes to bed (75 s)
    SLEEP_TICKS = 299        # the bed's "sleep" action

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.mode = "routine"                # routine | angry | chase
        self.committed = False               # reacting to a trick / catching: not interruptible
        self.sleeping: Optional[Entity] = None
        self.angry_left = 0
        self.angry_room: Optional[str] = None
        self._seen = 0
        self._noise_n = 0
        self._fired = set()
        self._wake = False
        self._awake_ticks = 0
        self._cool: Dict[str, int] = {}
        self._pending_alarm: Optional[str] = None
        self._interest_cursor = 0
        self._wander_count = 0

    # ---------------------------------------------------------------- per tick
    def tick(self):
        w = self.world
        if not w.ai or w.finished is not None:
            Entity.tick(self)                # keeps animating, but no thinking
            return
        if self.mode == "angry" and self.angry_left > 0:
            self.angry_left -= 1
        if self.sleeping is None:
            self._awake_ticks += 1
        self._sense()
        super().tick()
        if self.script is None and not self.locked:
            self._start(self._think())

    def _nact(self, name: str) -> Optional[dict]:
        return self._own_actions(self).get(name)

    # ---------------------------------------------------------------- senses
    def _sense(self):
        w = self.world
        wd = w.woody
        for n, room, level in w.recent_noise(self._noise_n):
            self._noise_n = n
            if self.sleeping is not None:
                if level >= 1 and (room == self.room or level >= 2):
                    self._wake = True                            # "wakeup" / "alarm" triggers
                    self.angry_room = room
            elif level >= 2 and not self.committed and not self.locked:
                self._queue_alarm(room)

        if self.sleeping is not None or self.committed or self.locked or not self.visible:
            self._seen = 0
            return

        if wd is not None and self._can_see(wd):
            # Same-room discovery is immediate: Woody freezes and the neighbour catches him.
            wd._start(wd.panic_from(self))
            self._seen = 0
            self._start(self._chase())
            return

        self._seen = 0
        self._check_triggers()

    def _can_see(self, wd: Actor) -> bool:
        return wd.visible and wd.hiding is None and wd.room == self.room and not wd.locked

    def _pet_in(self, room: str) -> Optional[Actor]:
        return next((e for e in self.world.entities
                     if isinstance(e, Actor) and e.name in ("dog", "chili")
                     and e.visible and e.room == room), None)

    def _queue_alarm(self, room: str):
        """Queue a pet alarm until the current object action has finished."""
        self.angry_room = room
        self.angry_left = max(self.angry_left, self.world.angry_ticks())
        self.mode = "angry"
        self.running = True

        pet = self._pet_in(room)
        wd = self.world.woody
        if (pet is not None and wd is not None and wd.visible
                and wd.hiding is None and wd.room == room):
            # Woody woke the animal: face the pet and freeze in fear, even if
            # the neighbour is still travelling to the pet's room.
            wd._start(wd.panic_from(pet))

        self._pending_alarm = room
        if self.script is None:
            self._start(self._think())

    def _ref_point(self, e: Entity):
        hs = e.objdef.hotspots.get("neighbor") if e.objdef else None
        if hs:
            return e.x + hs[0], e.y + hs[1]
        r = e.rect() or e.region_rect()
        if r:
            return r[0] + r[2] / 2 - e.origin[0], r[1] + r[3] / 2 - e.origin[1]
        return e.x, e.y

    def _check_triggers(self):
        """<trigger object=".." position="nearobj" type="once|always"/> of the neighbour."""
        w = self.world
        for behavior, trigs in w.level.triggers.get("neighbor", {}).items():
            for t in trigs:
                name = t.attrs.get("object")
                if not name or t.attrs.get("position") != "nearobj":
                    continue
                e = w.entity_named(name, self.room)
                if e is None:
                    continue
                key = (name, w.trick_epoch.get(name, 0))
                if key in self._fired or self._cool.get(name, 0) > w.tick_no:
                    continue
                x, y = self._ref_point(e)
                if abs(self.x - x) <= self.TRIGGER_DX and abs(self.y - y) <= 90:
                    if t.attrs.get("type") == "once":
                        self._fired.add(key)
                    else:
                        self._cool[name] = w.tick_no + 120
                    self._start(self._slip(e) if behavior in SLIP else self._use_object(e))
                    return

    # ---------------------------------------------------------------- brain
    def _think(self):
        self.running = self.mode in ("angry", "chase")

        if self._pending_alarm is not None:
            room = self._pending_alarm
            self._pending_alarm = None
            self.angry_room = room
            return self._inspect_alarm(room)

        if self.mode == "angry":
            if self.angry_left <= 0:
                self.mode, self.running = "routine", False
                self.world.tricks_in_row = 0
            else:
                return self._search_round()
        return self._routine_step()

    def _inspect_alarm(self, room: str):
        """Enter the pet's room, search if Woody is not visible, then scold it."""
        w = self.world
        self.mode = "angry"
        self.running = True
        yield from self._hops(room)
        if self.room != room:
            return

        wd = w.woody
        woody_visible = wd is not None and self._can_see(wd)

        pet = self._pet_in(room)
        if pet is None:
            return

        if not woody_visible and "search" in self.animations:
            yield from self._run_action(None, {
                "actoranim": "search",
                "actornextanim": f"ms{self.facing}",
                "time": "auto",
            })

        # Walk normally to the pet, then play the easy shout.
        target_x = min(max(pet.x, self._lane(room)[0]), self._lane(room)[1])
        yield from self._walk(target_x, w.path_y(w.rooms[room], target_x))

        if "shout_easy1" in self.animations:
            yield from self._run_action(None, {
                "actoranim": "shout_easy1",
                "actornextanim": f"ms{self.facing}",
                "time": "auto",
            })

        # If Woody woke the pet and froze in fear, chase him after scolding it.
        if (wd is not None and wd.visible and wd.hiding is None
                and wd.room == room and wd.locked):
            yield from self._chase()

    def _trick_done(self, obj: Entity):
        """The neighbour fell for a trick: rating, anger."""
        w = self.world
        w.trick_triggered(obj.name)
        self.mode = "angry"
        self.angry_left = w.angry_ticks(obj.name)
        self.angry_room = self.room

    def _idle(self, ticks: int):
        self.stand(self.world.rng.choice((0, 2)))
        for _ in range(ticks):
            yield

    def _floor_trick_names(self):
        trig = self.world.level.triggers.get("neighbor", {})
        return {t.attrs.get("object") for b in SLIP & set(trig) for t in trig[b]}

    def _routine_step(self):
        w, rng = self.world, self.world.rng
        self.running = False
        floor = self._floor_trick_names()

        # Prepared traps always have priority, nearest/current-room trap first.
        pending = [
            e for e in w.entities
            if e.kind == "object" and e.visible and e.room
            and e.name in w.level.tricks
            and (e.name, w.trick_epoch.get(e.name, 0)) not in self._fired
        ]
        pending.sort(key=lambda e: (
            e.room != self.room,
            abs(e.x - self.x),
        ))

        yield from self._idle(rng.randint(12, 24) if pending else rng.randint(18, 36))

        if self.mode != "routine":
            return

        if pending:
            e = pending[0]
            if e.name.endswith("clock") and self._bed() is not None:
                yield from self._sleep(short=True)
            elif e.name in floor:
                yield from self._cross(e)
            else:
                yield from self._use_object(e)
            self._wander_count = 0
            return

        if self._awake_ticks > self.SLEEP_EVERY and self._bed() is not None:
            yield from self._sleep()
            return

        interests = [
            e for e in w.entities
            if e.kind == "object" and e.visible and e.room and e.objdef
            and e.name not in w.level.tricks
            and "hideout" not in " ".join(e.objdef.flags)
            and e.objdef.hotspots.get("neighbor")
            and any(rank(n) is not None for n in self._own_actions(e))
        ]
        interests.sort(key=lambda e: (e.room != self.room, e.room or "", e.x))

        # Stable object selection is preferred; wandering is a bounded fallback.
        if interests and (self._wander_count >= 2 or rng.random() < 0.85):
            self._wander_count = 0
            self._interest_cursor = (self._interest_cursor + 1) % len(interests)
            yield from self._use_object(interests[self._interest_cursor])
        elif self._wander_count < 2:
            self._wander_count += 1
            yield from self._wander()
        else:
            self._wander_count = 0
            yield from self._idle(rng.randint(20, 40))

    def _lane(self, room: str):
        r = self.world.rooms[room]
        return sorted((r.path1[0], r.path2[0]))

    def _wander(self, room: Optional[str] = None):
        w = self.world
        rooms = [r for r in w.rooms if r != "fro"]
        room = room or w.rng.choice(rooms)
        yield from self._hops(room)
        if self.room == room:
            lo, hi = self._lane(room)
            x = w.rng.uniform(lo + 60, hi - 60)
            yield from self._walk(x, w.path_y(w.rooms[room], x))

    def _cross(self, e: Entity):
        """Walk across the room so that he passes over a floor trick (banana, soap, marbles ...)."""
        yield from self._hops(e.room)
        if self.room != e.room:
            return
        lo, hi = self._lane(e.room)
        x = self._ref_point(e)[0]
        goal = hi - 40 if self.x < x else lo + 40                # the far side: he walks over it
        yield from self._walk(goal, self.world.path_y(self.world.rooms[e.room], goal))

    # ---------------------------------------------------------------- using objects / tricks
    def _use_object(self, obj: Entity):
        w = self.world
        acts = self._own_actions(obj)
        yield from self._hops(obj.room)
        if self.room != obj.room:
            return
        if obj.objdef and obj.objdef.hotspots.get("neighbor"):
            yield from self._stand_at(obj)
        else:
            x = self._ref_point(obj)[0]
            yield from self._walk(x, w.path_y(w.rooms[self.room], x))
        is_trick = obj.name in w.level.tricks
        try:
            for n in sorted((n for n in acts if rank(n) is not None), key=rank):
                yield from self._run_action(obj, acts[n])
            if is_trick:
                self.committed = True
                yield from self._react(obj, acts)
                self._trick_done(obj)
                for n in UNDO:
                    if n in acts:
                        yield from self._run_action(obj, acts[n])
                        break
                w.undo_trick(obj.name)
        finally:
            self.committed = False

    def _react(self, obj: Entity, acts: dict):
        """His reaction to a trick that worked: shout loudness follows the trick's quota."""
        w = self.world
        name = obj.name.lower()
        prims = ()
        if not (set(acts) & OWN_REACTION):
            prims = next((p for keys, p in REACTIONS if any(k in name for k in keys)), ("surprise",))
        for p in prims:
            if self._nact(p):
                yield from self._run_action(None, self._nact(p))
        q = w.level.tricks.get(obj.name)
        pts = q.quotas[0] if q else 0
        shout = "shout0_light" if pts < 10 else "shout0_medium" if pts < 20 else "shout0"
        if self._nact(shout):
            yield from self._run_action(None, self._nact(shout))

    def _slip(self, e: Entity):
        """He steps on a floor trick: slips, gets hurt, cleans it up (if there is a clean action)."""
        w = self.world
        self.committed = True
        try:
            d = self.facing if self.facing in (1, 3) else 1
            for n in (f"slip{d}", "hurt"):
                if self._nact(n):
                    yield from self._run_action(None, self._nact(n))
            self._trick_done(e)
            clean = self._own_actions(e).get("clean")
            if clean:
                yield from self._stand_at(e)
                yield from self._run_action(e, clean)
            w.undo_trick(e.name)
        finally:
            self.committed = False

    # ---------------------------------------------------------------- sleeping
    def _bed(self) -> Optional[Entity]:
        """His own bed (flag neighbor_hideout) - the bed object carries the sleep animations."""
        w = self.world
        wd = w.woody
        for e in w.entities:
            if (e.kind == "object" and e.visible and e.objdef and "neighbor_hideout" in e.objdef.flags
                    and "enter" in e.animations and "sleep" in e.animations):
                if wd is not None and wd.hiding is e:
                    return None
                return e
        return None

    def _sleep(self, short: bool = False):
        w = self.world
        self._awake_ticks = 0
        trick_bed = next((e for e in w.entities if e.kind == "object" and e.visible and e.room
                          and e.name in w.level.tricks and "sleep" in self._own_actions(e)), None)
        if trick_bed is not None:                                # pins in the bed ...
            yield from self._use_object(trick_bed)
            return
        bed = self._bed()
        if bed is None:
            yield from self._idle(30)
            return
        yield from self._hops(bed.room)
        yield from self._stand_at(bed)
        self._wake = False
        self.sleeping, self.visible = bed, False
        bed.set_anim("enter", then="sleep")
        cactus = None
        try:
            for _ in range(self.SLEEP_TICKS // 4 if short else self.SLEEP_TICKS):
                if self._wake:
                    break
                yield
            if not self._wake:                                   # the alarm clock rings
                cactus = yield from self._alarm_clock(bed)
        finally:
            self.sleeping = None
        bed.set_anim("leave", then=bed.base_anim)
        while bed.anim != bed.base_anim:
            yield
        self.visible = True
        self.stand(1)
        self.committed = cactus is not None
        try:
            if cactus is not None:                               # he hit a cactus instead of the clock
                yield from self._react(cactus, {"hurt": 1})
                self._trick_done(cactus)
                w.undo_trick(cactus.name)
        finally:
            self.committed = False
        if self.mode != "angry":
            self.mode = "angry"                                  # he is up and in a bad mood
            self.angry_left = w.angry_ticks() // (1 if self._wake else 2)
        self._wake = False

    def _alarm_clock(self, bed: Entity):
        """Returns the clock entity if it was a trick (cactus), else None."""
        w = self.world
        clock = next((e for e in w.entities if e.kind == "object" and e.visible and e.room == bed.room
                      and e.name.endswith("clock") and "ring" in e.animations), None)
        if clock is None:
            return None
        clock.set_anim("ring", then=clock.base_anim)
        hit = "hit_cactus" if "cactus" in clock.name else "hit_alarm"
        if hit in bed.animations:
            bed.set_anim(hit, then="sleep")
            for _ in range(len(bed.animations[hit].frames) + 1):
                yield
        return clock if clock.name in w.level.tricks else None   # a cactus instead of the clock

    # ---------------------------------------------------------------- angry: searching
    def _search_round(self):
        w = self.world
        wd = w.woody
        rng = w.rng
        target = self.angry_room if self.angry_room in w.rooms and self.angry_room != "fro" else None
        self.angry_room = None
        if target is None:
            others = [r for r in w.rooms if r not in ("fro", self.room)] or [self.room]
            target = (wd.room if wd is not None and wd.room in w.rooms and wd.room != "fro"
                      and rng.random() < 0.5 else rng.choice(others))
        yield from self._wander(target)
        if self.mode != "angry":
            return
        if self._nact("search"):
            yield from self._run_action(None, self._nact("search"))
        # Hideouts are safe: searching the room must never reveal/catch hidden Woody.

    # ---------------------------------------------------------------- chase and catch
    def _move_x(self, tx: float, follow_line: bool = False):
        d = 1 if tx > self.x else 3
        self._gait(d)
        step = self.speed_of(d)
        self.x = tx if abs(tx - self.x) <= step else self.x + (step if d == 1 else -step)
        if follow_line:
            self.y = self.world.path_y(self.world.rooms[self.room], self.x)

    def _move_y(self, ty: float):
        d = 2 if ty > self.y else 0
        self._gait(d)
        step = self.speed_of(d)
        self.y = ty if abs(ty - self.y) <= step else self.y + (step if d == 2 else -step)

    def _chase_step(self, tx: float, ty: float):
        """One running step towards Woody - like everybody he only moves along an axis."""
        line = self.world.path_y(self.world.rooms[self.room], self.x)
        if abs(tx - self.x) > 1.0:
            if abs(self.y - line) > 0.5:
                self._move_y(line)
            else:
                self._move_x(tx, follow_line=True)
        else:
            self._move_y(ty)

    def _chase(self):
        w = self.world
        wd = w.woody
        self.mode, self.running = "chase", False
        d = 1 if wd.x > self.x else 3
        if self._nact(f"discover{d}"):
            yield from self._run_action(None, self._nact(f"discover{d}"))
        self.running = True
        lost = 0
        try:
            while True:
                can_catch_here = (
                    self._can_see(wd)
                    or (wd.panicked and wd.visible and wd.hiding is None and wd.room == self.room)
                )
                if can_catch_here:
                    lost = 0
                    if abs(wd.x - self.x) <= self.CATCH_DX and abs(wd.y - self.y) <= 40:
                        yield from self._catch(wd)
                        return
                    self._chase_step(wd.x, wd.y)
                elif (wd.visible and wd.hiding is None and wd.room != self.room
                      and wd.room in w.rooms and not wd.locked):
                    doors = w.room_path(self.room, wd.room)
                    if doors and w.claim_door(doors[0], self):
                        try:
                            yield from self._through(doors[0])   # follow only through an unclaimed pair
                        finally:
                            w.release_door(doors[0], self)
                        lost = 0
                        continue
                    lost += 1
                else:
                    lost += 1
                if lost > 90:
                    break
                yield
        finally:
            self.running = False
        self.mode = "angry"                                      # lost him: search the house
        self.angry_left = max(self.angry_left, w.angry_ticks() // 2)
        self.angry_room = wd.room if wd.room != "fro" else None

    def _catch(self, wd: Actor):
        """He grabs Woody: the kill animation (Woody is part of it), then Woody restarts."""
        w = self.world
        self.committed, self.running = True, False
        dx = wd.x - self.x
        d = 2 if abs(dx) < 20 else (1 if dx > 0 else 3)
        if f"kill{d}" not in self.animations:
            d = 1
        if wd.room == self.room:                                 # the brawl happens where Woody stands
            self.x, self.y = wd.x, wd.y
        if wd.script is not None:
            wd.script.close()
            wd.script = None
        wd.visible, wd.locked, wd.progress = False, True, None
        try:
            yield from self._run_action(None, {"actoranim": f"kill{d}", "actornextanim": "ms2"})
        finally:
            self.committed = False
        self.mode, self.angry_left = "routine", 0
        w.on_caught(by_neighbor=True)


class Pet(Actor):
    """Dog / Chili: asleep until Woody walks in their room (noise 1), then they bark (noise 2 -
    the neighbour comes) a few times and fall asleep again."""

    BARKS = 3

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._noise_n = 0
        self.awake = False

    def tick(self):
        w = self.world
        if w.ai and w.finished is None and self.script is None:
            for n, room, level in w.recent_noise(self._noise_n):
                self._noise_n = n
                if level == 1 and room == self.room and not self.awake:
                    self.script = self._alert()
                    break
        super().tick()

    def _alert(self):
        w = self.world
        self.awake = True
        act = self._own_actions(self).get("wakeup") or {"actoranim": "wakeup"}
        yield from self._run_action(None, act)
        try:
            for _ in range(self.BARKS):
                wd = w.woody
                d = 1 if wd is not None and wd.x > self.x else 3
                bark = dict(self._own_actions(self).get(f"bark{d}") or {"actoranim": f"bark{d}"})
                bark.pop("actornextanim", None)
                w.make_noise(self.room, 2)
                yield from self._run_action(None, bark)
                if wd is None or wd.room != self.room:
                    break
            if "fallasleep" in self.animations:
                self.set_anim("fallasleep")
                for _ in range(len(self.animations["fallasleep"].frames)):
                    yield
        finally:
            if "sleep" in self.animations:
                self.base_anim = "sleep"
                self.set_anim("sleep")
            self.awake = False
