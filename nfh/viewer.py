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
    hold Shift         sneak (slow, silent) - footsteps wake the sleeping neighbour and the pets
    N                  neighbour and pets AI on / off (to look around in peace)
"""
from __future__ import annotations
import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

from nfh import level_names, load_game, load_level
from nfh.assets import SoundBank, SpriteBank
from nfh.engine import ANIM_FPS, World
from nfh.render import render_world
from nfh import gui

HUD_H = 56
BAR_H = 136                       # original-style HUD / inventory panel at the bottom
SLOT_W = 84
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
        self.sound_cache = {}
        self.music_on = False
        self.follow = True
        self.view_h = VIEW_H
        self.icon_cache = {}
        self.hud_state = gui.HudState()
        self.toast = None
        self._load()

    # ---- level handling -----------------------------------------------------
    def _load(self):
        w = self.world = World(load_level(self.names[self.level_idx]), self.bank)
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
                        pygame.mixer.music.load(p)
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
                snd = pygame.mixer.Sound(path) if path else None
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

    def _icon(self, item: str, state: str):
        key = (item, state)
        if key not in self.icon_cache:
            img = self.bank.get(f"I_{item}_{state}", "gui/inv")
            if img is None and state != "norm":
                img = self.bank.get(f"I_{item}_norm", "gui/inv")
            self.icon_cache[key] = (pygame.image.frombuffer(img.tobytes(), img.size, "RGBA")
                                    if img is not None else None)
        return self.icon_cache[key]

    def _slots(self):
        """Visible inventory hitboxes for the current five-item HUD page."""
        sh = self.screen.get_height()
        per_page = 5
        pages = max(1, (len(self.world.inventory) + per_page - 1) // per_page)
        self.hud_state.inventory_page = max(0, min(self.hud_state.inventory_page, pages - 1))
        first = self.hud_state.inventory_page * per_page
        panel_y = sh - 136
        return [(it, (155 + i * 88, panel_y + 55, 70, 65))
                for i, it in enumerate(self.world.inventory[first:first + per_page])]

    def _slot_at(self, pos):
        for it, (x, y, w, h) in self._slots():
            if x <= pos[0] < x + w and y <= pos[1] < y + h:
                return it
        return None

    def _hover_label(self, pos):
        """Text like "Open fridge" / "Use chips with cookie box" for the thing under the mouse."""
        w = self.world
        if pos[1] >= self.screen.get_height() - BAR_H or pos[1] < HUD_H:
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
        action = gui.hud_scroll_at(self.screen, pos)
        if action == "inventory-left":
            self.hud_state.inventory_page = max(0, self.hud_state.inventory_page - 1)
            return
        if action == "inventory-right":
            pages = max(1, (len(w.inventory) + 4) // 5)
            self.hud_state.inventory_page = min(pages - 1, self.hud_state.inventory_page + 1)
            return
        if pos[1] >= sh - BAR_H:                              # HUD / inventory panel
            it = self._slot_at(pos)
            self.item = None if (it is None or it == self.item) else it
            return
        if pos[1] < HUD_H:
            return
        x, y = self._to_level(*pos)
        room = w.room_at(x, y)
        hit = w.hit_test(x, y, room) if room else None
        if hit is wd:
            hit = None
        if self.item and wd and room:                         # use the picked item here
            r = w.rooms[room]
            target = hit if hit is not None and hit.kind == "object" else None
            if wd.use_item(self.item, target, room, x - r.offset[0], y - r.offset[1]):
                self.item = None                              # refused (busy entering a hideout): keep it
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
            return False
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
        elif key == K.K_n:                          # neighbour / pets AI on or off
            w.ai = not w.ai
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
        self._draw_progress()
        self._draw_overlays(sw, sh)
        w = self.world
        wd = w.woody
        title = f"{self.names[self.level_idx]}   Woody: {wd.room if wd else '-'}" \
                f"   {'overview' if self.mode == 'overview' else 'camera ' + ('follows' if self.follow else 'free')}"
        title += "   [paused]" if w.paused else ""
        title += "" if w.ai else "   [AI off]"
        self.screen.blit(self.big.render(title, True, (240, 240, 240)), (10, 6))
        secs = int(w.time_left / ANIM_FPS)
        status = f"Rating {int(w.score)}" + (f"/{w.minquota}" if w.minquota else "") \
                 + (f"    Time {secs // 60}:{secs % 60:02d}" if w.time_left else "") + f"    Lives {w.lives}"
        st = self.big.render(status, True, (255, 226, 120))
        self.screen.blit(st, (sw - st.get_width() - 12, 6))
        self._draw_toast_and_end(sw, sh)
        hover = self._hover_label(pygame.mouse.get_pos())
        if hover:
            info = hover
        elif self.item:
            info = f"Use {w.item_name(self.item)} with ...  (click an object or the floor, right click cancels)"
        elif self.selected is not None:
            names = self.selected.anim_names()
            cur = names[self.sel_anim % len(names)] if names else "-"
            info = f"{self.selected.label()} ({self.selected.kind})  playing: {self.selected.anim}   " \
                   f"next animation: {cur}  (, . change, Enter play)"
        else:
            info = "click: walk / door / open / look   inventory: click an item, then an object   " \
                   "arrows: scroll  C: follow  wheel: zoom  Tab: overview  PgUp/PgDn: level  M: music"
        self.screen.blit(self.font.render(info, True, (190, 190, 200)), (10, 34))
        pygame.display.flip()

    def _draw_toast_and_end(self, sw, sh):
        w = self.world
        if w.toasts:
            self.toast = (w.toasts.pop(), 2.0)
        if self.toast and self.toast[1] > 0:
            t = self.big.render("TV rating " + self.toast[0], True, (255, 226, 120))
            self.screen.blit(t, ((sw - t.get_width()) // 2, HUD_H + 56))
        if w.finished:
            msg = {"won": "EPISODE COMPLETE!", "lost": "THE NEIGHBOUR WINS THIS ONE"}[w.finished]
            sub = f"Rating {int(w.score)}" + (f" of {w.minquota} needed" if w.minquota else "") \
                  + "   -   R: play again   PgDn: next level"
            a, b = self.big.render(msg, True, (255, 255, 255)), self.font.render(sub, True, (230, 230, 230))
            bw = max(a.get_width(), b.get_width()) + 60
            bx, by = (sw - bw) // 2, sh // 2 - 50
            pygame.draw.rect(self.screen, (30, 24, 20), (bx, by, bw, 90))
            pygame.draw.rect(self.screen, (255, 226, 120), (bx, by, bw, 90), 3)
            self.screen.blit(a, (bx + (bw - a.get_width()) // 2, by + 16))
            self.screen.blit(b, (bx + (bw - b.get_width()) // 2, by + 54))

    def _draw_progress(self):
        """Trick progress bar above Woody's head, at the 'progressbar' hotspot from
        objects.xml. Shown only while he prepares a trick on an object - not when he
        just puts something (marbles, banana, soap ...) on the floor."""
        wd = self.world.woody
        if wd is None or getattr(wd, "progress", None) is None or not wd.visible:
            return
        hs = (wd.objdef.hotspots.get("progressbar") if wd.objdef else None) or (-30, -130)
        x, y = self._to_screen(wd.origin[0] + wd.x + hs[0], wd.origin[1] + wd.y + hs[1])
        bar_x, bar_y = int(x), int(y)
        if self.hud_state.image("ingame_progress_back") is not None:
            gui.draw_progress(self.screen, self.hud_state, wd.progress, bar_x, bar_y, self.scale)
        else:
            bw = max(24, int(60 * self.scale))          # fallback when the artwork pack is absent
            bh = max(4, int(7 * self.scale))
            pygame.draw.rect(self.screen, (24, 20, 16), (bar_x, bar_y, bw, bh))
            pygame.draw.rect(self.screen, (94, 196, 80),
                             (bar_x + 1, bar_y + 1, int((bw - 2) * wd.progress), bh - 2))
            pygame.draw.rect(self.screen, (240, 232, 200), (bar_x, bar_y, bw, bh), 1)

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
        # Original-style HUD and five-item paged inventory.
        mouse = pygame.mouse.get_pos()
        slot_states = {it: ("pres" if it == self.item else "norm") for it in w.inventory}
        for it, (x, y, iw, ih) in self._slots():
            if it != self.item and x <= mouse[0] < x + iw and y <= mouse[1] < y + ih:
                slot_states[it] = "hov"
        item_images = [self._icon(it, slot_states[it]) for it in w.inventory]
        gui.draw_hud(self.screen, w, self.hud_state, item_images)
        # Woody's comment
        if w.message:
            lines = self._wrap(w.message[0], max(200, sw - 120))
            lh = 30
            bw = max(self.big.size(l)[0] for l in lines) + 28
            bx, by = (sw - bw) // 2, HUD_H + 8
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
                elif ev.type == pygame.KEYDOWN:
                    running = self._key(ev.key) and running
                elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    self._click(ev.pos)
                elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
                    self.item = None
                elif wheel is not None and ev.type == wheel:
                    self.view_h = min(max(self.view_h * (0.9 if ev.y > 0 else 1.1), 300), 995)
            w = self.world
            if w.woody is not None and hasattr(pygame, "key"):       # hold Shift: Woody sneaks (silent)
                w.woody.sneak = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            if self.toast:
                self.toast = (self.toast[0], self.toast[1] - dt)
            w.update(dt)
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
    """Yield every directory below `roots` (breadth first, limited depth)."""
    seen = set()
    for root in roots:
        root = os.path.abspath(root)
        queue = [(root, 0)]
        while queue:
            d, lvl = queue.pop(0)
            if d in seen or not os.path.isdir(d):
                continue
            seen.add(d)
            yield d
            if lvl >= depth:
                continue
            try:
                for name in sorted(os.listdir(d)):
                    if name.startswith(".") or name in ("__pycache__", "nfh", "tools", "examples"):
                        continue
                    queue.append((os.path.join(d, name), lvl + 1))
            except OSError:
                pass


def discover(png=None, tga=None, sfx=None):
    """Resolve the asset folders: explicit arguments first, then search around the current folder
    and the project folder (so nested folders like NFH1/NFH1 and unzipped archives are found)."""
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    explicit = [p for p in (png, tga, sfx) if p]
    roots = explicit + [os.getcwd(), project, os.path.dirname(project)]
    found = {"png": None, "tga": None, "sfx": None}
    wanted_png, wanted_tga, wanted_sfx = png, tga, sfx
    for d in _scan(roots):
        kind = _sprite_kind(d)
        if kind and not found[kind]:
            found[kind] = d
        if _sound_root(d):
            if not found["sfx"] or ("high" in os.path.basename(d).lower()
                                    and "high" not in os.path.basename(found["sfx"]).lower()):
                found["sfx"] = d
        if all(found.values()) and not explicit:
            break
    return found


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
    ap.add_argument("--png", help="folder with the PNG sprites (the one that directly contains anc/, kit/, gui/ ...)")
    ap.add_argument("--tga", help="folder with the original .tga sprites (gfxdata)")
    ap.add_argument("--sfx", help="sound folder (sfxdata or sfxdatahigh); default: sfxdatahigh if found")
    ap.add_argument("--level", default="level_pig")
    ap.add_argument("--size", default="1280x760")
    a = ap.parse_args(argv)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = [os.getcwd(), here, os.path.dirname(here)]
    f_png, f_tga, f_sfx = _scan(roots)
    png = a.png or (f_png[0] if f_png else None)
    tga = a.tga or (f_tga[0] if f_tga else None)
    sfx = a.sfx
    if not sfx and f_sfx:
        sfx = next((p for p in f_sfx if "high" in os.path.basename(p).lower()), f_sfx[0])
    for label, path in (("png", png), ("tga", tga)):
        if path and not os.path.isdir(path):
            sys.exit(f"--{label} folder does not exist: {os.path.abspath(path)}")
    if not png and not tga:
        sys.exit("No sprites found.\n"
                 "Searched (4 levels deep) in:\n  " + "\n  ".join(roots) + "\n"
                 "Unzip NFH1.zip (and/or gfxdata.zip) somewhere, then run:\n"
                 "  python play.py --png <folder that directly contains anc, kit, gui, house ...>\n"
                 "Tip: for NFH1.zip that is usually the inner NFH1/NFH1 folder.")
    print("sprites (png):", png, "\nsprites (tga):", tga, "\nsounds:", sfx)
    w, h = (int(v) for v in a.size.lower().split("x"))
    Viewer(SpriteBank(png, tga), SoundBank(sfx if sfx and os.path.isdir(sfx) else None), a.level, (w, h)).run()


if __name__ == "__main__":
    main()
