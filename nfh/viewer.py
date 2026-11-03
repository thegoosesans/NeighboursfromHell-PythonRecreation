"""pygame window: watch the levels, click around, make Woody walk and use doors.

    python play.py [--png <NFH1 folder>] [--tga <gfxdata folder>] [--sfx <sfxdata folder>] [--level level_pig]
(without arguments it searches the current and project folders for the unzipped sprites / sounds)

Controls
    mouse click        click the floor = Woody walks there (through doors if it is another room),
                       click a door = he walks to it and goes through,
                       click an object = look at it / open it / take what is inside / hide in it
    inventory          click an item in the bar at the bottom, then click an object or the floor to use it
                       (right click cancels); items that belong together make a "trick"
    arrows             scroll the camera by hand      C   camera follows Woody again    wheel   zoom
    Tab                overview of the whole house    PgUp / PgDn   previous / next level
    , and .            previous / next animation of the selected thing      Enter   play it
    Space              pause      M   music on/off      R   restart level      Esc   quit
"""
from __future__ import annotations
import argparse
import io
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

from nfh import level_names, load_game, load_level
from nfh.assets import SoundBank, SpriteBank
from nfh.engine import World
from nfh.render import render_world
from nfh.ui import Hud
from nfh import data as game_data
from nfh.source import Source, find_data_dir
from nfh.menu import Menu, Progress
from nfh import level_sets

HUD_H = 0                          # the level fills the window; the interface (nfh.ui.Hud) overlays its bottom edge
BAR_H = 0
VIEW_H = 640                       # level pixels shown vertically (the original shows ~800x600)


def wd_or_none(w):
    return w.woody


class Viewer:
    def __init__(self, bank: SpriteBank, sounds: SoundBank, level: str, size=(1280, 760)):
        pygame.init()
        try:
            pygame.mixer.init()
            self.audio = True
        except pygame.error:
            self.audio = False
        self.bank, self.sounds = bank, sounds
        self.names = level_names()
        self.level_idx = self.names.index(level) if level in self.names else 0
        pygame.display.set_caption(load_game().title)
        self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 22)
        self.big = pygame.font.SysFont(None, 28)
        self.small = pygame.font.SysFont(None, 19)
        self.hud = Hud(bank, self.font, self.small)
        self.sound_cache = {}
        self.music_on = False
        self.follow = True
        self.view_h = VIEW_H
        self.icon_cache = {}
        self.info = {l.name: l for st in level_sets() for l in st.levels}
        self.menu = Menu(bank, Progress(os.path.join(os.path.expanduser("~"), ".nfh_python")))
        self.state = "menu"                       # menu | game
        self._load()

    # ---- level handling -----------------------------------------------------
    def _load(self):
        w = self.world = World(load_level(self.names[self.level_idx]), self.bank)
        self.elapsed = 0.0
        self.mode = "camera"                      # camera (scrolling, follows Woody) | overview
        self.selected = None
        self.sel_anim = 0
        self.item = None                          # inventory item picked up with the mouse
        self.rect = (0, 0, 1, 1)
        self.scale, self.origin = 1.0, (0, 0)
        self.cam = [0.0, 0.0]
        self.follow = True
        if w.woody:
            self.cam = list(self._woody_point())
            door = w.door("fro/anc")              # intro: Woody walks into the house
            if door is not None and w.woody.room == "fro":
                w.woody.use_door(door)

    def _level_over(self) -> bool:
        """Time is up (leveldata `time` in animation steps) or every point is earned -> results screen."""
        info = self.info.get(self.names[self.level_idx])
        limit = (info.time / 12.0) if info and info.time else 0
        if self.world.score >= 100 or (limit and self.elapsed >= limit):
            self.menu.show_result(self.names[self.level_idx], min(100, int(self.world.score)),
                                  info.minquota if info else 0)
            self.menu.started, self.state = False, "menu"
            return True
        return False

    def _woody_point(self):
        wd = self.world.woody
        if wd is None or wd.room not in self.world.rooms:
            return (self.world.level.layout.size[0] / 2, self.world.level.layout.size[1] / 2)
        r = self.world.rooms[wd.room]
        return (r.offset[0] + wd.x, r.offset[1] + wd.y - 60)

    def _music(self):
        if not self.audio:
            return
        if self.music_on:
            for p in self.sounds.music():
                if "ingame1_normal" in p:
                    try:
                        pygame.mixer.music.load(io.BytesIO(self.sounds.read(p)), "mp3")
                        pygame.mixer.music.play(-1)
                    except pygame.error:
                        pass
                    break
        else:
            pygame.mixer.music.stop()

    def _play(self, name: str):
        if not self.audio:
            return
        snd = self.sound_cache.get(name)
        if snd is None and name not in self.sound_cache:
            path = self.sounds.path(name)
            try:
                snd = pygame.mixer.Sound(file=io.BytesIO(self.sounds.read(path))) if path else None
            except pygame.error:
                snd = None
            self.sound_cache[name] = snd
        if snd:
            snd.play()

    # ---- camera / coordinates ----------------------------------------------------
    def _view_rect(self):
        W, H = self.world.level.layout.size
        if self.mode == "overview":
            return (0, 0, W, H)
        sw, sh = self.screen.get_size()
        aspect = sw / max(1, sh - HUD_H - BAR_H)
        vh = min(self.view_h, H, W / aspect)
        vw = vh * aspect
        x0 = min(max(self.cam[0] - vw / 2, 0), W - vw)
        y0 = min(max(self.cam[1] - vh / 2, 0), H - vh)
        return (int(x0), int(y0), int(vw), int(vh))

    def _to_level(self, mx, my):
        return (self.rect[0] + (mx - self.origin[0]) / self.scale,
                self.rect[1] + (my - self.origin[1]) / self.scale)

    def _to_screen(self, x, y):
        return (self.origin[0] + (x - self.rect[0]) * self.scale,
                self.origin[1] + (y - self.rect[1]) * self.scale)

    def _in_hud(self, pos):
        return self.hud.zone(*self.screen.get_size()).collidepoint(pos)

    def _slot_at(self, pos):
        return self.hud.slot_at(self.world.inventory, pos, *self.screen.get_size())

    def _hover_label(self, pos):
        """Text like "Open fridge" / "Use chips with cookie box" for the thing under the mouse."""
        w = self.world
        if self._in_hud(pos):
            return ""
        x, y = self._to_level(*pos)
        room = w.room_at(x, y)
        hit = w.hit_test(x, y, room) if room else None
        if hit is wd_or_none(w):
            hit = None
        tname = hit.label() if hit is not None and hit.kind != "door" else (room or "")
        if self.item:
            return f"Use {w.item_name(self.item)} with {tname}".strip()
        if hit is None:
            return ""
        if hit.kind == "door":
            return "Go to " + (w.level.text(hit.name) or hit.name.split("/")[-1])
        std = hit.objdef.stdactions[0] if hit.objdef and hit.objdef.stdactions else ""
        verb = w.level.strings.get(std, {}).get("string", "")
        return f"{verb} {hit.label()}".strip()

    def _visible_rooms(self):
        x, y, w, h = self.rect
        out = set()
        for name in self.world.rooms:
            rx, ry, rw, rh = self.world.room_rect(name, 0)
            if rx < x + w and rx + rw > x and ry < y + h and ry + rh > y:
                out.add(name)
        return out

    # ---- input -------------------------------------------------------------------------
    def _click(self, pos):
        w = self.world
        wd = w.woody
        sh = self.screen.get_height()
        if self._in_hud(pos):                                 # interface: inventory tray
            it = self._slot_at(pos)
            self.item = None if (it is None or it == self.item) else it
            return
        x, y = self._to_level(*pos)
        room = w.room_at(x, y)
        hit = w.hit_test(x, y, room) if room else None
        if hit is wd:
            hit = None
        if self.item and wd and room:                         # use the picked item here
            r = w.rooms[room]
            target = hit if hit is not None and hit.kind == "object" else None
            wd.use_item(self.item, target, room, x - r.offset[0], y - r.offset[1])
            self.item = None
            return
        if hit is not None:
            self.selected, self.sel_anim = hit, 0
            if wd and hit.kind == "door":
                wd.use_door(hit)                              # walks there (through other rooms) and enters
                return
            if wd and hit.kind == "object" and hit.objdef and (hit.objdef.stdactions or hit.contents):
                wd.interact(hit)
                return
        else:
            self.selected = None
        if wd and room:
            r = w.rooms[room]
            wd.go_to(room, x - r.offset[0], y - r.offset[1])

    def _scroll(self, dx, dy):
        self.follow = False
        self.cam[0] += dx
        self.cam[1] += dy

    def _key(self, key):
        K = pygame
        w = self.world
        if key == K.K_ESCAPE:
            self.menu.started, self.menu.page, self.state = True, "main", "menu"
            return True
        if key == K.K_TAB:
            self.mode = "camera" if self.mode == "overview" else "overview"
        elif key == K.K_RIGHT:
            self._scroll(150, 0)
        elif key == K.K_LEFT:
            self._scroll(-150, 0)
        elif key == K.K_UP:
            self._scroll(0, -150)
        elif key == K.K_DOWN:
            self._scroll(0, 150)
        elif key == K.K_PAGEDOWN:
            self.level_idx = (self.level_idx + 1) % len(self.names); self._load()
        elif key == K.K_PAGEUP:
            self.level_idx = (self.level_idx - 1) % len(self.names); self._load()
        elif key == K.K_r:
            self._load()
        elif key == K.K_SPACE:
            w.paused = not w.paused
        elif key == K.K_m:
            self.music_on = not self.music_on; self._music()
        elif key in (K.K_f, K.K_c):
            self.follow = True
        elif key in (K.K_COMMA, K.K_PERIOD) and self.selected:
            names = self.selected.anim_names()
            if names:
                self.sel_anim = (self.sel_anim + (1 if key == K.K_PERIOD else -1)) % len(names)
        elif key in (K.K_RETURN, K.K_KP_ENTER) and self.selected:
            names = self.selected.anim_names()
            if names:
                self.selected.play(names[self.sel_anim % len(names)])
        return True

    # ---- drawing -----------------------------------------------------------------------
    def _draw(self):
        sw, sh = self.screen.get_size()
        self.screen.fill((18, 18, 22))
        self.rect = self._view_rect()
        img = render_world(self.world, self.rect)
        surf = pygame.image.frombuffer(img.tobytes(), img.size, "RGBA")
        avail_h = max(1, sh - HUD_H - BAR_H)
        self.scale = min(sw / img.width, avail_h / img.height)
        tw, th = max(1, int(img.width * self.scale)), max(1, int(img.height * self.scale))
        surf = pygame.transform.smoothscale(surf, (tw, th))
        self.origin = ((sw - tw) // 2, HUD_H + (avail_h - th) // 2)
        self.screen.blit(surf, self.origin)
        if self.selected is not None:
            r = self.selected.rect() or self.selected.region_rect()
            if r:
                x0, y0 = self._to_screen(r[0], r[1])
                pygame.draw.rect(self.screen, (255, 220, 60),
                                 (int(x0), int(y0), int(r[2] * self.scale), int(r[3] * self.scale)), 2)
        self._draw_overlays(sw, sh)
        w = self.world
        wd = w.woody
        hover = self._hover_label(pygame.mouse.get_pos())
        if hover:
            info = hover
        elif self.item:
            info = f"Use {w.item_name(self.item)} with ...   (right click cancels)"
        elif self.selected is not None:
            names = self.selected.anim_names()
            cur = names[self.sel_anim % len(names)] if names else "-"
            info = f"{self.selected.label()}   anim: {self.selected.anim}  next: {cur}  (, . Enter)"
        else:
            info = "Click to walk, open, look. Pick an item, then click an object."
        progress = min(1.0, w.score / 100.0)
        mood = 1 + min(3, int(progress * 4))
        name = self.names[self.level_idx] + ("  [paused]" if w.paused else "")
        self.hud.draw(self.screen, w.inventory, self.item, info, progress, mood, pygame.mouse.get_pos(), name)
        pygame.display.flip()

    def _wrap(self, text, width):
        lines, cur = [], ""
        for word in text.split():
            trial = (cur + " " + word).strip()
            if cur and self.big.size(trial)[0] > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        return lines + ([cur] if cur else [])

    def _draw_overlays(self, sw, sh):
        w = self.world
        # Woody's comment
        if w.message:
            lines = self._wrap(w.message[0], max(200, sw - 120))
            lh = 30
            bw = max(self.big.size(l)[0] for l in lines) + 28
            bx, by = (sw - bw) // 2, 14
            pygame.draw.rect(self.screen, (250, 246, 225), (bx, by, bw, lh * len(lines) + 12))
            pygame.draw.rect(self.screen, (60, 50, 40), (bx, by, bw, lh * len(lines) + 12), 2)
            for i, l in enumerate(lines):
                self.screen.blit(self.big.render(l, True, (40, 30, 20)), (bx + 14, by + 8 + i * lh))

    # ---- main loop ----------------------------------------------------------------------
    def run(self):
        running = True
        wheel = getattr(pygame, "MOUSEWHEEL", None)
        while running:
            dt = self.clock.tick(60) / 1000.0
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif self.state == "menu":
                    act = self.menu.event(ev)
                    if act and act[0] == "quit":
                        running = False
                    elif act and act[0] == "resume":
                        self.state = "game"
                    elif act and act[0] == "start":
                        self.level_idx = self.names.index(act[1])
                        self._load()
                        self.menu.started, self.state = True, "game"
                elif ev.type == pygame.KEYDOWN:
                    running = self._key(ev.key) and running
                elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    self._click(ev.pos)
                elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
                    self.item = None
                elif wheel is not None and ev.type == wheel and self.hud.wheel(
                        self.world.inventory, pygame.mouse.get_pos(), *self.screen.get_size(), ev.y):
                    pass
                elif wheel is not None and ev.type == wheel:
                    self.view_h = min(max(self.view_h * (0.9 if ev.y > 0 else 1.1), 300), 995)
            if self.state == "menu":
                self.menu.draw(self.screen)
                pygame.display.flip()
                continue
            w = self.world
            w.update(dt)
            if not w.paused:
                self.elapsed += dt
            if self._level_over():
                continue
            if w.caught:
                self._load()
                continue
            if self.follow and self.mode == "camera":          # smooth scrolling after Woody
                tx, ty = self._woody_point()
                k = min(1.0, dt * 4)
                self.cam[0] += (tx - self.cam[0]) * k
                self.cam[1] += (ty - self.cam[1]) * k
            visible = self._visible_rooms() if self.mode == "camera" else set(w.rooms)
            for snd, room in w.drain_sounds():
                if room is None or room in visible:
                    self._play(snd)
            self._draw()
        pygame.quit()


def _scan(roots, depth=4):
    """Find sprite folders (contain anc/ + gui/ sub-folders) and sound folders (contain sfx/)."""
    png, tga, sfx = [], [], []
    seen = set()
    for root in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        base = root.count(os.sep)
        for d, dirs, files in os.walk(root):
            if d.count(os.sep) - base >= depth:
                dirs[:] = []
            if d in seen:
                continue
            seen.add(d)
            names = {x.lower() for x in dirs}
            if {"anc", "gui"} <= names:
                sample = next((f for _d, _s, fs in os.walk(os.path.join(d, "anc")) for f in fs), "")
                (png if sample.lower().endswith(".png") else tga).append(d)
            if "sfx" in names and os.path.isdir(os.path.join(d, "sfx")):
                sfx.append(d)
    return png, tga, sfx


def main(argv=None):
    ap = argparse.ArgumentParser(description="Neighbours From Hell viewer")
    ap.add_argument("--game", help="the original game's data folder (the one with gamedata.bnd, gfxdata.bnd, "
                                   "sfxdata.bnd); XML, sprites and sounds are then read from the originals")
    ap.add_argument("--png", help="folder with PNG sprites (preferred over the originals' .tga)")
    ap.add_argument("--tga", help="folder or .bnd with the original .tga sprites (gfxdata)")
    ap.add_argument("--sfx", help="folder or .bnd with the sounds (sfxdata / sfxdatahigh)")
    ap.add_argument("--no-original", action="store_true", help="do not look for an installed original game")
    ap.add_argument("--level", default="level_pig")
    ap.add_argument("--size", default="1280x760")
    a = ap.parse_args(argv)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = [os.getcwd(), here, os.path.dirname(here)]
    f_png, f_tga, f_sfx = _scan(roots)

    src = None
    if not a.no_original:
        d = find_data_dir(a.game)
        if a.game and d is None:
            sys.exit(f"--game: no gamedata.bnd found in {os.path.abspath(a.game)} (or its data/ folder)")
        if d:
            src = Source(d)
            game_data.use(src)                         # all XML now comes from the original gamedata.bnd

    png = a.png or (f_png[0] if f_png else None)
    tga = a.tga or (src.gfx if src and src.gfx else (f_tga[0] if f_tga else None))
    sfx = a.sfx or (src.sfx if src and src.sfx else None)
    if not sfx and f_sfx:
        sfx = next((p for p in f_sfx if "high" in os.path.basename(p).lower()), f_sfx[0])
    for label, path in (("png", png), ("tga", tga)):
        if path and not os.path.exists(path):
            sys.exit(f"--{label} does not exist: {os.path.abspath(path)}")
    if not png and not tga:
        sys.exit("No sprites found.\n"
                 "Point the viewer at the original game:  python play.py --game <folder with gamedata.bnd>\n"
                 "or at unpacked sprites:  python play.py --png <folder that directly contains anc, kit, gui ...>\n"
                 "Searched (4 levels deep) in:\n  " + "\n  ".join(roots))
    print("data   :", src.dir if src else "bundled copies (converted from the editor's basegame mod)")
    print("sprites:", png or tga, "\nsounds :", sfx)
    w, h = (int(v) for v in a.size.lower().split("x"))
    Viewer(SpriteBank(png, tga), SoundBank(sfx if sfx and os.path.exists(sfx) else None), a.level, (w, h)).run()


if __name__ == "__main__":
    main()
