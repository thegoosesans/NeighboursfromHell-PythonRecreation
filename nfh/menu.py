"""Main menu, level selection and the end-of-level screen, drawn with the original gui/main artwork.

Everything is laid out on the original 800x600 canvas and scaled to the window (letterboxed).
`Menu.event()` returns an action tuple: ("start", level) | ("resume",) | ("quit",) | ("menu",).
"""
from __future__ import annotations
import json
import os
from typing import Dict, List, Optional, Tuple

import pygame

from .model import level_sets

CANVAS = (800, 600)
GOLD, INK, PAPER = (255, 224, 120), (46, 34, 24), (240, 232, 205)


def button_art(level: str) -> str:
    """level_DIY -> button_diy, tutorial_2 -> button_tut2"""
    if level.startswith("tutorial_"):
        return "button_tut" + level.split("_")[1]
    return "button_" + level.replace("level_", "").lower()


def medal(score: int, minquota: int) -> str:
    if score >= 100:
        return "gold"
    if score >= minquota + (100 - minquota) // 2:
        return "silver"
    if score >= minquota:
        return "bronze"
    return "default"


class Progress:
    """best score per level, kept in its own folder (never in the original game's save folder)"""

    def __init__(self, path: Optional[str]):
        self.path = os.path.join(path, "progress.json") if path else None
        self.best: Dict[str, int] = {}
        try:
            if self.path and os.path.exists(self.path):
                self.best = {k: int(v) for k, v in json.load(open(self.path, encoding="utf-8")).items()}
        except (OSError, ValueError):
            pass

    def record(self, level: str, score: int):
        if score > self.best.get(level, -1):
            self.best[level] = score
            try:
                if self.path:
                    os.makedirs(os.path.dirname(self.path), exist_ok=True)
                    json.dump(self.best, open(self.path, "w", encoding="utf-8"))
            except OSError:
                pass


class Menu:
    def __init__(self, bank, progress: Progress, started: bool = False):
        self.bank, self.progress = bank, progress
        self.started = started                       # a level is running -> "Resume" is offered
        self.page = "main"                           # main | levels | result
        self.cache: Dict[Tuple[str, str], Optional[pygame.Surface]] = {}
        self.font = pygame.font.SysFont(None, 34)
        self.mid = pygame.font.SysFont(None, 26)
        self.small = pygame.font.SysFont(None, 20)
        self.huge = pygame.font.SysFont(None, 72)
        self.canvas = pygame.Surface(CANVAS)
        self.levels: List[Tuple[str, int]] = [(l.name, l.minquota) for s in level_sets() for l in s.levels]
        self.selected = next((n for n, _ in self.levels if n == "level_pig"), self.levels[0][0])
        self.hot: List[Tuple[pygame.Rect, tuple]] = []
        self.result = (0, 0, "")
        self.scale, self.offset = 1.0, (0, 0)

    # ---- art -----------------------------------------------------------------------------
    def img(self, name: str, folder: str = "gui/main") -> Optional[pygame.Surface]:
        key = (folder, name)
        if key not in self.cache:
            im = self.bank.get(name, folder)
            self.cache[key] = pygame.image.frombuffer(im.tobytes(), im.size, "RGBA").convert_alpha() if im else None
        return self.cache[key]

    def text(self, s, font, color, center=None, topleft=None, shadow=True):
        t = font.render(s, True, color)
        r = t.get_rect(center=center) if center else t.get_rect(topleft=topleft)
        if shadow:
            self.canvas.blit(font.render(s, True, (20, 14, 10)), r.move(2, 2))
        self.canvas.blit(t, r)
        return r

    def put(self, name, pos, folder="gui/main", center=False):
        s = self.img(name, folder)
        if s is None:
            return pygame.Rect(pos, (0, 0))
        r = s.get_rect(center=pos) if center else s.get_rect(topleft=pos)
        self.canvas.blit(s, r)
        return r

    # ---- coordinate mapping -----------------------------------------------------------------
    def _fit(self, sw, sh):
        self.scale = min(sw / CANVAS[0], sh / CANVAS[1])
        w, h = int(CANVAS[0] * self.scale), int(CANVAS[1] * self.scale)
        self.offset = ((sw - w) // 2, (sh - h) // 2)

    def _pos(self, p):
        return ((p[0] - self.offset[0]) / self.scale, (p[1] - self.offset[1]) / self.scale)

    # ---- pages ----------------------------------------------------------------------------------
    def show_result(self, level: str, score: int, minquota: int):
        self.result = (score, minquota, level)
        self.progress.record(level, score)
        self.page = "result"

    def _button(self, rect: pygame.Rect, action: tuple, mouse):
        self.hot.append((rect, action))
        return rect.collidepoint(mouse)

    def _pill(self, r: pygame.Rect, hover: bool):
        if hover:
            self.put("mainmenu_btn_hov", r.topleft)
        else:
            veil = pygame.Surface(r.size, pygame.SRCALPHA)
            pygame.draw.rect(veil, (20, 24, 40, 150), veil.get_rect(), border_radius=r.height // 2)
            self.canvas.blit(veil, r.topleft)

    def _draw_main(self, mouse):
        self.put("mainmenu", (0, 0))
        logo = self.img("logo_main", "gui/logo")
        if logo:
            self.canvas.blit(logo, logo.get_rect(midtop=(400, 8)))
        items = []
        if self.started:
            items.append(("Resume", ("resume",)))
        items += [("Play", ("levels",)), ("Quit", ("quit",))]
        y = 250
        for label, act in items:
            r = pygame.Rect(0, 0, 333, 54)
            r.center = (400, y)
            hov = self._button(r, act, mouse)
            self._pill(r, hov)
            self.text(label, self.font, GOLD if hov else PAPER, center=r.center)
            y += 66
        self.text("click to choose", self.small, (255, 255, 255), center=(400, 580), shadow=True)

    # (x, y of the first button, buttons per row, row height, scale) for each page box of newgameleft.png
    BOXES = [(30, 66, 3, 76, 1.0), (30, 168, 3, 76, 1.0), (28, 337, 4, 50, 0.68), (28, 438, 4, 50, 0.68)]

    def _draw_levels(self, mouse):
        self.canvas.fill((24, 40, 44))
        self.put("newgameleft", (0, 0))
        self.put("newgameright", (400, 0))
        self.text("Choose a level", self.font, PAPER, topleft=(110, 16))
        for (x0, y0, per, dy, k), st in zip(self.BOXES, level_sets()):
            for i, l in enumerate(st.levels):
                w, h = int(88 * k), int(64 * k)
                x, y = x0 + (i % per) * (w + 8), y0 + (i // per) * dy
                best = self.progress.best.get(l.name)
                state = "perfect" if best is not None and best >= 100 else ("passed" if best is not None and best >= l.minquota else "normal")
                r = pygame.Rect(x, y, w, h)
                hov = self._button(r, ("select", l.name), mouse)
                art = self.img(f"{button_art(l.name)}_{state}" + ("_hover" if hov else ""))
                if art:
                    self.canvas.blit(pygame.transform.smoothscale(art, (w, h)) if k != 1.0 else art, r.topleft)
                if l.name == self.selected:
                    pygame.draw.rect(self.canvas, GOLD, r.inflate(6, 6), 3, border_radius=6)
        # right page: the picture goes into the black frame, title on the yellow banner
        pic = self.img(self.selected)
        if pic:
            self.canvas.blit(pic, (428, 120))
        minq = dict(self.levels)[self.selected]
        self.text(self.selected.replace("level_", "").replace("_", " ").title(), self.font, INK, topleft=(440, 66), shadow=False)
        self.text(f"Needed to pass: {minq} points", self.mid, INK, topleft=(440, 290), shadow=False)
        best = self.progress.best.get(self.selected)
        self.text(f"Best: {best} points" if best is not None else "Not played yet", self.mid, INK, topleft=(440, 320), shadow=False)
        self.text("Enter or the green arrow starts", self.small, INK, topleft=(440, 350), shadow=False)
        # the round buttons are part of the page art, only the click areas are needed
        start = pygame.Rect(0, 0, 54, 54)
        start.center = (692, 560)
        self._button(start, ("start", self.selected), mouse)
        back = pygame.Rect(0, 0, 54, 54)
        back.center = (36, 560)
        self._button(back, ("menu",), mouse)

    def _draw_result(self, mouse):
        score, minq, level = self.result
        kind = medal(score, minq)
        self.put("endscreen_" + kind, (0, 0))
        veil = pygame.Surface((420, 150), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 150))
        self.canvas.blit(veil, (190, 400))
        verdict = {"gold": "Perfect!", "silver": "Great!", "bronze": "Level passed", "default": "Try again"}[kind]
        self.text(verdict, self.huge, GOLD, center=(400, 438))
        self.text(f"{score} points   (needed: {minq})", self.mid, PAPER, center=(400, 490))
        r = pygame.Rect(0, 0, 333, 54)
        r.center = (400, 530)
        hov = self._button(r, ("menu",), mouse)
        self._pill(r, hov)
        self.text("Continue", self.font, GOLD if hov else PAPER, center=r.center)

    def draw(self, screen: pygame.Surface):
        sw, sh = screen.get_size()
        self._fit(sw, sh)
        mouse = self._pos(pygame.mouse.get_pos())
        self.hot = []
        {"main": self._draw_main, "levels": self._draw_levels, "result": self._draw_result}[self.page](mouse)
        screen.fill((0, 0, 0))
        w, h = int(CANVAS[0] * self.scale), int(CANVAS[1] * self.scale)
        screen.blit(pygame.transform.smoothscale(self.canvas, (w, h)), self.offset)

    # ---- input --------------------------------------------------------------------------------------
    def event(self, ev) -> Optional[tuple]:
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                if self.page == "levels":
                    self.page = "main"
                elif self.page == "main":
                    return ("resume",) if self.started else ("quit",)
            elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.page == "levels":
                    return ("start", self.selected)
                if self.page == "result":
                    self.page = "main"
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            p = self._pos(ev.pos)
            for rect, act in reversed(self.hot):
                if rect.collidepoint(p):
                    if act[0] == "select":
                        self.selected = act[1]
                    elif act[0] == "levels":
                        self.page = "levels"
                    elif act[0] == "menu":
                        self.page = "main"
                    else:
                        return act
                    return None
        return None
