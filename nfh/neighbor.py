"""The neighbour: patrols the house, falls for tricks, notices Woody and noise.

Which of the original's rules are known and which are guesses:
* known (data): the `<trigger>` entries in trigger.xml (noise level, "room"/"house"/"nearobj", always/once),
  the trick quotas (points) and `angrytime`, the neighbour's animations (mg*, ms*, surprise, shout*, slip1 ...).
* guessed (the logic lives in the protected game.exe): how long he stays in a room, which room he picks next,
  how close Woody may get before he is spotted.  The constants below are easy to tune.
"""
from __future__ import annotations
import random
from typing import List, Optional, Set

from .peep import PeepScript

STAY = (3.0, 7.0)               # seconds he lingers in a room
SPOT_DIST = 170                 # Woody is seen when he is this close (px) and not hidden
CAUGHT_WAIT = 2.5               # seconds until the level restarts after Woody was caught
ANIM_FPS = 12.0                 # same as engine.ANIM_FPS (angrytime is counted in animation steps)
NEAR = 90                       # px: "nearobj" triggers (the banana peel)
REACT = ("shout0", "doubletake1", "surprise", "shout0_medium", "shout2")


class NeighborAI:
    def __init__(self, world):
        self.w = world
        self.actor = next((e for e in world.entities if e.kind == "actor" and e.name == "neighbor"), None)
        self.timer = random.uniform(*STAY)
        self.victims: Set[str] = set()            # tricks he has already fallen for
        self.noise_room: Optional[str] = None
        self.caught = 0.0
        self.angry = 0.0                          # seconds of rage left (he runs and hunts Woody)
        lay = world.level.layout
        self.level_angry = (lay.angrytime if lay else 0) / ANIM_FPS
        self.triggers = world.level.triggers.get("neighbor", {})
        self.peep = PeepScript(self) if getattr(world.level, "name", "") == "level_peep" else None

    # ---- events from the world -----------------------------------------------------------
    def noise(self, room: Optional[str], level: int = 1):
        """Something made a noise in `room`; reacts according to trigger.xml ("alarm" = anywhere in the house)."""
        if self.actor is None or self.actor.busy:
            return
        for name, trigs in self.triggers.items():
            for t in trigs:
                a = t.attrs
                if int(a.get("noise", "0") or 0) and level >= int(a["noise"]):
                    if a.get("position") == "house" or a.get("position") == "room" and room == self.actor.room:
                        self.noise_room = room
                        return

    def _enrage(self, steps=None):
        secs = (steps / ANIM_FPS) if steps else self.level_angry
        self.angry = max(self.angry, secs)
        self.actor.gait = "mr"

    def _near_trap(self):
        """trigger.xml "nearobj": he steps on something lying in the room (the banana peel) and slips."""
        a = self.actor
        for beh, trigs in self.triggers.items():
            for t in trigs:
                obj = t.attrs.get("object")
                if t.attrs.get("position") != "nearobj" or not obj or obj in self.victims or obj not in self.w.tricks:
                    continue
                e = self.w.entity_named(obj, a.room)
                if e is not None and abs(e.x - a.x) < NEAR and abs(e.y - a.y) < NEAR:
                    return obj
        return None

    def _slip(self, obj: str):
        self.victims.add(obj)
        trick = self.w.level.tricks.get(obj)
        self.w.score += trick.quotas[0] if trick else 0
        self._enrage(trick.angrytime if trick and trick.angrytime else None)
        name = "slip3" if self.actor.facing == 3 else "slip1"
        if self._anim_ok(name):
            yield from self._play(name)
        for _ in range(12):
            yield

    def fall_for(self, obj: str):
        """He falls for trick `obj` once: its quota is scored and his rage starts (tricks.xml)."""
        if obj in self.victims:
            return
        self.victims.add(obj)
        trick = self.w.level.tricks.get(obj)
        self.w.score += trick.quotas[0] if trick else 0
        self._enrage(trick.angrytime if trick and trick.angrytime else None)
        for _ in range(12):
            yield

    def _hunt(self):
        """Angry: run to Woody's room and look around."""
        a, wd = self.actor, self.w.woody
        if wd is None or wd.room not in self.w.rooms:
            return
        yield from a._go(wd.room, *self._centre(wd.room))
        if a.room == wd.room and self._anim_ok("search"):
            yield from self._play("search")

    # ---- helpers ---------------------------------------------------------------------------
    def _trick_rooms(self) -> List[str]:
        out = []
        for t in self.w.tricks:
            room = t.split("/")[0]
            if t not in self.victims and room in self.w.rooms:
                out.append(room)
        return out

    def _centre(self, room: str):
        r = self.w.rooms[room]
        return (r.path1[0] + r.path2[0]) / 2, r.path1[1]

    def _anim_ok(self, name: str) -> bool:
        return name in self.actor.animations

    def _play(self, name: str):
        a = self.actor
        a.set_anim(name, then=a.base_anim)
        for _ in range(max(1, len(a.animations[name].frames))):
            yield

    # ---- scripts ---------------------------------------------------------------------------------
    def _visit(self, room: str, x: float, y: float):
        a = self.actor
        yield from a._go(room, x, y)
        if a.room != room:
            return
        for t in list(self.w.tricks):                 # fall for every unseen trick in this room
            if t.split("/")[0] != room or t in self.victims:
                continue
            self.victims.add(t)
            trick = self.w.level.tricks.get(t)
            self.w.score += trick.quotas[0] if trick else 0
            self._enrage(trick.angrytime if trick and trick.angrytime else None)
            anim = "slip1" if "banana" in t and self._anim_ok("slip1") else \
                next((n for n in random.sample(REACT, len(REACT)) if self._anim_ok(n)), None)
            if anim:
                yield from self._play(anim)
            for _ in range(24):
                yield
        for _ in range(int(random.uniform(*STAY) * 12)):
            yield

    def _catch(self):
        a = self.actor
        self.caught = CAUGHT_WAIT
        self.w.say(self.w.level.text("caught") or "Caught! Mr. Rottweiler saw you!", CAUGHT_WAIT)
        if self._anim_ok("shout2"):
            yield from self._play("shout2")

    # ---- per frame ---------------------------------------------------------------------------------
    def update(self, dt: float):
        a, w = self.actor, self.w
        if a is None:
            return
        if self.caught > 0:
            self.caught -= dt
            if self.caught <= 0:
                w.caught = True
            return
        wd = w.woody
        if (wd is not None and wd.visible and wd.hiding is None and wd.room == a.room and not a.locked
                and abs(wd.x - a.x) < SPOT_DIST * (1.5 if self.angry > 0 else 1) and abs(wd.y - a.y) < SPOT_DIST * 0.6):
            a.script = self._catch()
            self.caught = CAUGHT_WAIT
            return
        if self.angry > 0:
            self.angry -= dt
            if self.angry <= 0:
                self.angry, a.gait = 0.0, "mg"
        trap = self._near_trap()
        if trap:
            a.script = self._slip(trap)
            return
        if a.busy:
            return
        if self.angry > 0 and wd is not None and wd.room != a.room:
            a.script = self._hunt()
            return
        if self.noise_room is not None:
            room, self.noise_room = self.noise_room, None
            a.script = self._visit(room, *self._centre(room))
            return
        if self.peep is not None:                      # level_peep: the original's scripted routine
            a.script = self.peep.step()
            return
        self.timer -= dt
        if self.timer > 0:
            return
        self.timer = random.uniform(*STAY)
        pending = self._trick_rooms()
        pool = pending or [r for r in w.rooms if r != a.room]
        if pool:
            room = random.choice(pool)
            a.script = self._visit(room, *self._centre(room))
