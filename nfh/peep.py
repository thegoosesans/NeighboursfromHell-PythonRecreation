"""level_peep: the neighbour's routine, ported from the original game's code.

game.exe implements every level as a native class.  The one for "peep" (the step function at 0x45b177 in
the unpacked image) is a state machine: it switches on a state number 0..9 stored in the object (+0x10),
keeps the previous state in +0x14 and a retry counter in +0x18.  `FUN_0045aef0(new, old)` changes state,
`FUN_00471290(name)` is `isObjectPresent(name)` and `FUN_004713d0(level, a, b)` asks whether object `a`
has been replaced by trick `b`.  The action names it hands to the dispatcher (`FUN_00470f10`) are the
`<action>`s of level_peep/objects.xml, so the animations come from the data, not from this file.

    1 (and 0)  reset the retry counter, walk to lir/sofa                       -> 2
    2          lir/sofa_fartbag prepared  -> "enter", then "surprise" on it     -> 1
               otherwise                  -> "sit" (the 2nd and 4th time "sit_remo"), retry += 1;
                                             after the 5th attempt                -> 3
    3          walk to kit/binoculars (or kit/binoculars_glue) if either is there, else to kit/window  -> 4
    4          kit/binoculars_glue present -> "peep_glue"      (the glued binoculars: a trick)
               kit/binoculars present      -> "peep"
               otherwise                   -> "peep_no_binoculars" at kit/window                   -> 5
    5          back to lir/sofa                                                                      -> 6
    6          lir/tv has become lir/twistedantenna -> 7, otherwise                                   -> 1
    7          "repair" the antenna and shout                                                        -> 8
    8          finished with the antenna                                                             -> 1
    9          error state (the level was started in an unexpected mode); sets a flag on the level.

What is not read from the code: when a trick stops counting as "prepared" (assumed: once he has reacted),
how the original scores the tricks (it uses the quotas of tricks.xml,
applied here when he falls for one) and what exactly states 7/8 do with the antenna beyond the actions.
"""
from __future__ import annotations

SOFA = "lir/sofa"
SOFA_TRICK = "lir/sofa_fartbag"
BINOCULARS = "kit/binoculars"
BINOCULARS_TRICK = "kit/binoculars_glue"
WINDOW = "kit/window"
ANTENNA = "lir/twistedantenna"
SITS_BEFORE_PEEPING = 5          # `retry > 4` in state 2
REMOTE_ATTEMPTS = (1, 3)         # FUN_0045b020: "sit_remo" on these retries, "sit" on the others


class PeepScript:
    def __init__(self, ai):
        self.ai = ai
        self.w = ai.w
        self.state = 1
        self.retry = 0

    # ---- object helpers -------------------------------------------------------------------------
    def present(self, name: str):
        """isObjectPresent(): the entity if it is visible in the house, else None."""
        return self.w.entity_named(name)

    def _act(self, actor, obj, name: str):
        """Walk to `obj` and play its action `name` (from objects.xml) like the original's dispatcher."""
        yield from actor._hops(obj.room)
        if actor.room != obj.room:
            return
        yield from actor._stand_at(obj)
        act = actor._own_actions(obj).get(name)
        if act is not None:
            yield from actor._run_action(obj, act)

    def _walk_to(self, actor, obj):
        yield from actor._hops(obj.room)
        if actor.room == obj.room:
            yield from actor._stand_at(obj)

    # ---- the state machine ----------------------------------------------------------------------
    def step(self):
        """One state of the routine as a script for the actor; sets the next state."""
        a, w = self.ai.actor, self.w
        sofa = self.present(SOFA_TRICK) or self.present(SOFA)
        if self.state in (0, 1):
            self.retry = 0
            self.state = 2
            if sofa is not None:
                yield from self._walk_to(a, sofa)
            return
        if self.state == 2:
            if SOFA_TRICK in w.tricks and self.present(SOFA_TRICK) is not None:
                obj = self.present(SOFA_TRICK)
                yield from self._act(a, obj, "enter")
                yield from self.ai.fall_for(SOFA_TRICK)
                yield from self._act(a, obj, "surprise")
                w.tricks.remove(SOFA_TRICK)                       # spent (assumed: the original clears it too)
                self.state = 1
                return
            if sofa is None:
                self.state = 3
                return
            name = "sit_remo" if self.retry in REMOTE_ATTEMPTS else "sit"
            yield from self._act(a, sofa, name)
            self.retry += 1
            if self.retry >= SITS_BEFORE_PEEPING:
                self.state = 3
            return
        if self.state == 3:
            target = self.present(BINOCULARS) or self.present(BINOCULARS_TRICK) or self.present(WINDOW)
            self.state = 4
            if target is not None:
                yield from self._walk_to(a, target)
            return
        if self.state == 4:
            self.state = 5
            glued, plain, window = (self.present(BINOCULARS_TRICK), self.present(BINOCULARS),
                                    self.present(WINDOW))
            if glued is not None:
                yield from self._act(a, glued, "peep_glue")
                yield from self.ai.fall_for(BINOCULARS_TRICK)
            elif plain is not None:
                yield from self._act(a, plain, "peep")
            elif window is not None:
                yield from self._act(a, window, "peep_no_binoculars")
            return
        if self.state == 5:
            self.state = 6
            if sofa is not None:
                yield from self._walk_to(a, sofa)
            return
        if self.state == 6:
            self.state = 7 if ANTENNA in w.tricks and self.present(ANTENNA) is not None else 1
            return
        if self.state == 7:
            self.state = 8
            ant = self.present(ANTENNA)
            if ant is not None:
                yield from self._act(a, ant, "repair")
                yield from self.ai.fall_for(ANTENNA)
                w.tricks.remove(ANTENNA)                          # spent (assumed)
            return
        if self.state == 8:
            self.state = 1
            return
        self.state = 1                                           # 9: nothing to do here
