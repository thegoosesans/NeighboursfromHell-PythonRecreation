"""Original-style in-game HUD helpers.

Extract the supplied ingame archive to:

    nfh/gfxdata/gui/ingame/

The viewer can then draw the inventory, neighbour face, level statistics and
red/yellow/green trick progress bar with these helpers. The helpers also work
without the optional artwork pack.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence

import pygame


IMAGE_DIRS = (
    Path(__file__).resolve().parent / "gfxdata" / "gui" / "ingame",
    Path(__file__).resolve().parents[1] / "gfxdata" / "gui" / "ingame",
)


class HudState:
    def __init__(self):
        self.inventory_page = 0
        self._images: Dict[str, Optional[pygame.Surface]] = {}

    def image(self, name: str) -> Optional[pygame.Surface]:
        if name in self._images:
            return self._images[name]
        for directory in IMAGE_DIRS:
            path = directory / f"{name}.png"
            if path.exists():
                self._images[name] = pygame.image.load(str(path)).convert_alpha()
                return self._images[name]
        self._images[name] = None
        return None


def progress_bar(state: HudState, fraction: float, scale: float = 1.0):
    """Return the gradient red-yellow-green bar for fraction 0..1."""
    back = state.image("ingame_progress_back")
    front = state.image("ingame_progress_front")
    if back is None:
        return None

    fraction = max(0.0, min(1.0, float(fraction)))
    width = max(1, int(back.get_width() * fraction))
    out = back.copy()
    if front is not None:
        crop = pygame.Rect(0, 0, min(width, front.get_width()), front.get_height())
        if crop.width > 0 and crop.height > 0:
            out.blit(front.subsurface(crop), (0, 0))

    if scale != 1.0:
        size = max(1, round(out.get_width() * scale)), max(1, round(out.get_height() * scale))
        out = pygame.transform.smoothscale(out, size)
    return out


def draw_progress(surface: pygame.Surface, state: HudState,
                  fraction: float, x: int, y: int, scale: float = 1.0):
    bar = progress_bar(state, fraction, scale)
    if bar is not None:
        surface.blit(bar, (x, y))


def _text(surface, font, value, x, y, color=(225, 245, 235)):
    surface.blit(font.render(str(value), True, color), (x, y))


def draw_hud(surface: pygame.Surface, world, state: HudState,
             item_images: Sequence[Optional[pygame.Surface]]):
    """Draw the bottom interface and five inventory slots for the active page.

    item_images must contain one inventory icon per item in world.inventory,
    in the same order.
    """
    sw, sh = surface.get_size()
    panel_y = sh - 136
    font = pygame.font.Font(None, 25)
    small = pygame.font.Font(None, 21)

    # A dark fallback remains usable when the optional interface art is absent.
    surface.fill((34, 30, 28), (0, panel_y, sw, 136))
    for name, pos in (
        ("interface_left_bottom", (0, panel_y + 66)),
        ("interface_center", (78, panel_y + 30)),
        ("interface_right", (sw - 180, panel_y + 16)),
    ):
        image = state.image(name)
        if image is not None:
            surface.blit(image, pos)

    # Neighbour mood face.
    nb = getattr(world, "neighbor", None)
    if nb is not None and nb.mode == "angry":
        face = "head_02_norm"
    elif getattr(world, "tricks_in_row", 0) > 0:
        face = "head_04_norm"
    elif getattr(world, "trick_counts", None):
        face = "head_03_norm"
    else:
        face = "head_01_norm"

    face_image = state.image(face)
    if face_image is not None:
        surface.blit(face_image, (10, panel_y + 4))

    per_page = 5
    pages = max(1, (len(item_images) + per_page - 1) // per_page)
    state.inventory_page = max(0, min(state.inventory_page, pages - 1))
    if pages > 1:
        left = state.image("scroll_left_std")
        right = state.image("scroll_right_std")
        if left is not None:
            surface.blit(left, (100, panel_y + 58))
        if right is not None:
            surface.blit(right, (sw - 255, panel_y + 58))

    first = state.inventory_page * per_page
    for i, icon in enumerate(item_images[first:first + per_page]):
        if icon is None:
            continue
        scaled = icon
        if icon.get_width() > 70 or icon.get_height() > 65:
            scaled = pygame.transform.smoothscale(icon, (64, 58))
        x = 155 + i * 88
        y = panel_y + 55 + (65 - scaled.get_height()) // 2
        surface.blit(scaled, (x, y))

    seconds = max(0, int(round(getattr(world, "time_left", 0) / 12)))
    _text(surface, font, f"{seconds // 60}:{seconds % 60:02d}", sw - 125, panel_y + 35)
    _text(surface, font, f"{len(world.inventory)}", sw - 126, panel_y + 68)
    _text(surface, small, f"{len(world.tricks)}", sw - 72, panel_y + 68)
    quota = max(1, getattr(world, "minquota", 0))
    percentage = min(100, int(world.score * 100 / quota)) if quota else int(world.score)
    _text(surface, small, f"{percentage}%", sw - 126, panel_y + 92)
    _text(surface, font, f"{getattr(world, 'tricks_in_row', 0)}", sw - 60, panel_y + 92)


def hud_scroll_at(surface: pygame.Surface, pos) -> Optional[str]:
    """Return inventory-left/inventory-right for a HUD mouse click."""
    sw, sh = surface.get_size()
    x, y = pos
    panel_y = sh - 136
    if panel_y + 45 <= y <= panel_y + 105:
        if 95 <= x <= 140:
            return "inventory-left"
        if sw - 260 <= x <= sw - 215:
            return "inventory-right"
    return None
