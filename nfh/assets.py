"""Sprite and sound lookup.

Sprites: pass the folder with your PNGs (NFH1) and/or the original gfxdata folder with .tga files.
Files are stored as ``<object name>/<image>`` (e.g. ``anc/pig/pig_0001``), which is exactly the
object name used in gfxdata.xml plus the image name, so objects are resolved without ambiguity.
PNGs are preferred, the original .tga (decoded with nfh.tga) is the fallback.
"""
from __future__ import annotations
import io
import os
import wave
from typing import Dict, Iterable, List, Optional, Tuple

from .source import SEP, archive, read_path

SOUND_EXTS = (".wav", ".ogg", ".mp3")


def _norm(s: str) -> str:
    return os.path.splitext(s.replace("\\", "/").lower())[0]


class _Index:
    """case-insensitive, extension-agnostic index of a folder tree."""

    def __init__(self, root: Optional[str], exts: Tuple[str, ...]):
        self.root = root
        self.full: Dict[str, str] = {}
        self.base: Dict[str, str] = {}
        if not root:
            return
        if os.path.isfile(root):                          # a .bnd / .zip archive: index its entries
            for n in archive(root).names():
                if n.endswith(exts):
                    self.full[_norm(n)] = root + SEP + n
                    self.base.setdefault(_norm(os.path.basename(n)), root + SEP + n)
            return
        # accept zip-style nesting (NFH1/NFH1/...) by indexing relative to the deepest common root
        for d, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(exts):
                    p = os.path.join(d, f)
                    self.full[_norm(os.path.relpath(p, root))] = p
                    self.base.setdefault(_norm(f), p)

    def find(self, name: str, folder: str = "") -> Optional[str]:
        if folder:
            hit = self.full.get(_norm(folder.strip("/") + "/" + os.path.basename(name)))
            if hit:
                return hit
        hit = self.full.get(_norm(name))
        return hit or self.base.get(_norm(os.path.basename(name)))

    def __len__(self):
        return len(self.full)


class SpriteBank:
    def __init__(self, png_root: Optional[str] = None, tga_root: Optional[str] = None):
        self.png = _Index(png_root, (".png",))
        self.tga = _Index(tga_root, (".tga",))
        self._cache: Dict[Tuple[str, str], object] = {}

    def path(self, image: str, folder: str = "") -> Optional[str]:
        return self.png.find(image, folder) or self.tga.find(image, folder)

    def get(self, image: str, folder: str = ""):
        """PIL RGBA image for `image` ('pig_0001.tga') of object `folder` ('anc/pig'); None if absent."""
        from PIL import Image
        key = (folder.lower(), _norm(image))
        if key in self._cache:
            return self._cache[key]
        img = None
        p = self.png.find(image, folder)
        if p:
            img = Image.open(io.BytesIO(read_path(p))).convert("RGBA")
        else:
            p = self.tga.find(image, folder)
            if p:
                from .tga import load_tga
                img = load_tga(p)
        self._cache[key] = img
        return img

    def missing(self, level) -> List[str]:
        """Sprites referenced by a LevelData that cannot be found."""
        return sorted({f"{obj}/{g.image}" for obj, files in level.gfx.items() for g in files
                       if self.path(g.image, obj) is None})


class SoundBank:
    def __init__(self, root: Optional[str]):
        self.index = _Index(root, SOUND_EXTS)

    def path(self, name: str) -> Optional[str]:
        return self.index.find(name)

    def read(self, path: str) -> bytes:
        return read_path(path)

    def load_wav(self, name: str):
        p = self.path(name)
        if p is None or not p.lower().endswith(".wav"):
            return None
        with wave.open(io.BytesIO(read_path(p)), "rb") as w:
            return w.getparams(), w.readframes(w.getnframes())

    def music(self) -> List[str]:
        """Background-music files (.mp3, e.g. music/ingame1_fast.mp3); the XML does not list them."""
        return sorted(p for p in self.index.full.values() if p.lower().endswith(".mp3"))

    def missing(self, names: Iterable[str]) -> List[str]:
        return sorted({n for n in names if self.path(n) is None})
