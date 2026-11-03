"""In-game interface built from the original artwork (gui/ingame, gui/bubbles, gui/inv).

Layout, like the original: the level fills the whole window and the interface sits on its bottom edge -
left the neighbour's thought bubble + head, in the middle the inventory tray with the hint line on top,
right the control panel with the trick progress.
"""
from __future__ import annotations
from typing import List, Optional, Tuple

import pygame

SLOT_W, SLOT_H = 78, 57
TRAY_INNER = (33, 27, 394, 58)          # x, y, w, h of the usable area of interface_center.png
HINT_RECT = (30, 3, 398, 20)            # dark strip on top of the tray used for the hint line
BAR_RECT = (113, 7, 81, 34)             # where the progress bar sits inside interface_right.png


def _surface(img) -> Optional[pygame.Surface]:
    return pygame.image.frombuffer(img.tobytes(), img.size, "RGBA") if img is not None else None


class Hud:
    def __init__(self, bank, font: pygame.font.Font, small: pygame.font.Font):
        self.bank, self.font, self.small = bank, font, small
        self.scroll = 0                                           # first visible inventory slot
        self._img = {}
        self.scale = 1.0
        self.origin = (0, 0)                                      # top-left of the whole HUD strip

    # ---- art ---------------------------------------------------------------------------
    def img(self, name: str, folder: str = "gui/ingame") -> Optional[pygame.Surface]:
        key = (folder, name)
        if key not in self._img:
            self._img[key] = _surface(self.bank.get(name, folder))
        return self._img[key]

    def item_icon(self, item: str, state: str) -> Optional[pygame.Surface]:
        key = ("inv", item, state)
        if key not in self._img:
            img = self.bank.get(f"I_{item}_{state}", "gui/inv")
            if img is None and state != "norm":
                img = self.bank.get(f"I_{item}_norm", "gui/inv")
            self._img[key] = _surface(img)
        return self._img[key]

    # ---- geometry ------------------------------------------------------------------------
    def layout(self, sw: int, sh: int):
        """Place the three pieces on the bottom edge; returns dict of name -> pygame.Rect (screen px)."""
        self.scale = 1.0 if sw >= 1100 else max(0.7, sw / 1100)
        k = self.scale
        left_w, left_h = int(85 * k), int(134 * k)
        cen_w, cen_h = int(458 * k), int(89 * k)
        right_w, right_h = int(219 * k), int(129 * k)
        total = left_w + cen_w + right_w + int(16 * k)
        x0 = (sw - total) // 2
        y0 = sh - max(left_h, cen_h, right_h)
        self.origin = (x0, y0)
        return {
            "bubble": pygame.Rect(x0, sh - left_h, left_w, int(64 * k)),
            "head": pygame.Rect(x0, sh - int(70 * k), left_w, int(70 * k)),
            "center": pygame.Rect(x0 + left_w + int(8 * k), sh - cen_h, cen_w, cen_h),
            "right": pygame.Rect(x0 + left_w + cen_w + int(16 * k), sh - right_h, right_w, right_h),
        }

    def zone(self, sw: int, sh: int) -> pygame.Rect:
        r = self.layout(sw, sh)
        return r["head"].union(r["center"]).union(r["right"]).union(r["bubble"])

    def slots(self, items: List[str], sw: int, sh: int) -> List[Tuple[str, pygame.Rect]]:
        c = self.layout(sw, sh)["center"]
        k = self.scale
        ix, iy, iw, ih = (int(v * k) for v in TRAY_INNER)
        per = max(1, iw // int(SLOT_W * k))
        self.scroll = min(max(0, self.scroll), max(0, len(items) - per))
        out = []
        for i, it in enumerate(items[self.scroll:self.scroll + per]):
            out.append((it, pygame.Rect(c.x + ix + i * int(SLOT_W * k), c.y + iy, int(75 * k), int(57 * k))))
        return out

    def slot_at(self, items, pos, sw, sh) -> Optional[str]:
        for it, r in self.slots(items, sw, sh):
            if r.collidepoint(pos):
                return it
        return None

    def wheel(self, items, pos, sw, sh, dy: int) -> bool:
        if self.layout(sw, sh)["center"].collidepoint(pos):
            self.scroll -= dy
            return True
        return False

    # ---- drawing -------------------------------------------------------------------------
    def _blit(self, name, rect, folder="gui/ingame"):
        s = self.img(name, folder)
        if s is None:
            return None
        if self.scale != 1.0:
            s = pygame.transform.smoothscale(s, (max(1, int(s.get_width() * self.scale)),
                                                 max(1, int(s.get_height() * self.scale))))
        self._screen.blit(s, rect.topleft)
        return s

    def draw(self, screen: pygame.Surface, items: List[str], held: Optional[str], hint: str,
             progress: float, mood: int, mouse, title: str):
        self._screen = screen
        sw, sh = screen.get_size()
        r = self.layout(sw, sh)
        k = self.scale
        bub = self.img(f"bubble_{held}", "gui/bubbles") if held else None      # thought bubble: only while an item is held
        if bub is not None:
            self._blit("interface_left_bubble_norm", r["bubble"])
            screen.blit(pygame.transform.smoothscale(bub, r["bubble"].size) if k != 1.0 else bub, r["bubble"].topleft)
        self._blit(f"head_0{max(1, min(4, mood))}_norm", r["head"])
        self._blit("interface_center", r["center"])
        self._blit("interface_right", r["right"])
        # hint line on the dark strip of the tray
        c = r["center"]
        if hint:
            txt = self.small.render(hint, True, (226, 226, 200))
            while txt.get_width() > HINT_RECT[2] * k and len(hint) > 4:
                hint = hint[:-4] + "…"
                txt = self.small.render(hint, True, (226, 226, 200))
            screen.blit(txt, (c.x + int(HINT_RECT[0] * k), c.y + int((HINT_RECT[1] + 10) * k) - txt.get_height() // 2))
        # inventory items
        slots = self.slots(items, sw, sh)
        for it, sr in slots:
            hov = sr.collidepoint(mouse)
            state = "pres" if it == held else ("hov" if hov else "norm")
            icon = self.item_icon(it, state)
            if icon is not None:
                if k != 1.0:
                    icon = pygame.transform.smoothscale(icon, sr.size)
                screen.blit(icon, sr.topleft)
            if it == held:
                pygame.draw.rect(screen, (255, 220, 60), sr.inflate(4, 4), 2, border_radius=6)
        if not items:
            t = self.small.render("empty - open things to find items", True, (170, 170, 150))
            screen.blit(t, (c.x + int(TRAY_INNER[0] * k) + 28, c.y + int(TRAY_INNER[1] * k) + 20))
        elif self.scroll > 0 or len(items) > len(slots) + self.scroll:
            t = self.small.render(f"{self.scroll + 1}-{self.scroll + len(slots)} / {len(items)}", True, (190, 190, 170))
            screen.blit(t, (c.right - t.get_width() - int(40 * k), c.y + 6))
        # progress bar on the right panel (rainbow clipped to the achieved share)
        rr = r["right"]
        bx, by, bw, bh = (int(v * k) for v in BAR_RECT)
        self._blit("ingame_progress_back", pygame.Rect(rr.x + bx, rr.y + by, bw, bh))
        front = self.img("ingame_progress_front")
        if front is not None and progress > 0:
            w = max(1, int(front.get_width() * min(1.0, progress)))
            part = front.subsurface((0, 0, w, front.get_height()))
            if k != 1.0:
                part = pygame.transform.smoothscale(part, (max(1, int(w * k)), max(1, int(front.get_height() * k))))
            screen.blit(part, (rr.x + bx, rr.y + by))
        # level name above the right panel
        t = self.small.render(title, True, (235, 235, 215))
        shadow = self.small.render(title, True, (20, 20, 20))
        pos = (rr.centerx - t.get_width() // 2, rr.y - t.get_height() - 4)
        screen.blit(shadow, (pos[0] + 1, pos[1] + 1))
        screen.blit(t, pos)
