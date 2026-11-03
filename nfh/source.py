"""Read the original game's data folder directly.

The retail game keeps everything in `data/`: game.xml, the fonts and five `.bnd` files, which are plain
ZIP archives (no encryption):
    gamedata.bnd    all XML (levels, objects, animations, strings, dialogs ...)
    gfxdata.bnd     the sprites as .tga
    sfxdata.bnd / sfxdatahigh.bnd    sounds and music
    modloader.bnd   files added by the community mod loader (not used here)
Nothing is extracted to disk; `Archive.read()` pulls single files out of the zip on demand.
"""
from __future__ import annotations
import os
import zipfile
from typing import Dict, List, Optional

SEP = "@@"                                    # "<archive path>@@<name inside>" addresses a file in a .bnd


def _norm(name: str) -> str:
    return name.replace("\\", "/").strip("/").lower()


class Archive:
    """Case-insensitive access to a .bnd/.zip file or to a plain folder."""

    def __init__(self, path: str):
        self.path = path
        self._zip = zipfile.ZipFile(path) if os.path.isfile(path) else None
        self._names: Dict[str, str] = {}
        if self._zip:
            for n in self._zip.namelist():
                if not n.endswith("/"):
                    self._names[_norm(n)] = n
        elif os.path.isdir(path):
            for d, _dirs, files in os.walk(path):
                for f in files:
                    full = os.path.join(d, f)
                    self._names[_norm(os.path.relpath(full, path))] = full

    def names(self) -> List[str]:
        return list(self._names)

    def __contains__(self, name: str) -> bool:
        return _norm(name) in self._names

    def read(self, name: str) -> Optional[bytes]:
        real = self._names.get(_norm(name))
        if real is None:
            return None
        if self._zip:
            return self._zip.read(real)
        with open(real, "rb") as f:
            return f.read()


def split(path: str):
    """'a.bnd@@x/y.tga' -> ('a.bnd', 'x/y.tga');  a normal path -> (path, None)"""
    if SEP in path:
        a, b = path.split(SEP, 1)
        return a, b
    return path, None


_open: Dict[str, Archive] = {}


def archive(path: str) -> Archive:
    if path not in _open:
        _open[path] = Archive(path)
    return _open[path]


def read_path(path: str) -> bytes:
    """Bytes of a loose file or of 'archive.bnd@@inner/name'."""
    arc, inner = split(path)
    if inner is None:
        with open(path, "rb") as f:
            return f.read()
    data = archive(arc).read(inner)
    if data is None:
        raise FileNotFoundError(path)
    return data


class Source:
    """An installed copy of the game: `data/` with game.xml and the .bnd archives."""

    def __init__(self, data_dir: str):
        self.dir = data_dir
        bnd = os.path.join(data_dir, "gamedata.bnd")
        folder = os.path.join(data_dir, "gamedata")
        self.gamedata = archive(bnd) if os.path.isfile(bnd) else (archive(folder) if os.path.isdir(folder) else None)

    def xml(self, path: str) -> Optional[bytes]:
        """XML file by data path ('level_pig/anims', 'leveldata', 'game')."""
        name = path.replace("\\", "/") + ".xml"
        if self.gamedata is not None:
            b = self.gamedata.read(name)
            if b is not None:
                return b
        loose = os.path.join(self.dir, *name.split("/"))
        if os.path.isfile(loose):
            with open(loose, "rb") as f:
                return f.read()
        return None

    def level_dirs(self) -> List[str]:
        """Folder names (as spelled in the archive) that hold a level.xml: level_pig, tutorial_1 ..."""
        out = []
        if self.gamedata is not None:
            for n in self.gamedata.names():
                if n.endswith("/level.xml"):
                    out.append(self.gamedata._names[n].split("/")[0])
        return sorted(out)

    def bnd(self, *names: str) -> Optional[str]:
        """First existing archive (or folder) among `names` ('sfxdatahigh', 'sfxdata'), as a path."""
        for n in names:
            for cand in (os.path.join(self.dir, n + ".bnd"), os.path.join(self.dir, n)):
                if os.path.exists(cand):
                    return cand
        return None

    @property
    def gfx(self):
        return self.bnd("gfxdata")

    @property
    def sfx(self):
        return self.bnd("sfxdatahigh", "sfxdata")


def _is_data_dir(d: str) -> bool:
    return os.path.isfile(os.path.join(d, "gamedata.bnd")) or os.path.isdir(os.path.join(d, "gamedata"))


def find_data_dir(hint: Optional[str] = None) -> Optional[str]:
    """`hint` may be the game folder, its data/ folder or a folder containing it; without a hint the usual
    places are searched (NFH_DATA, the current folder, Documents/<any folder>/data, Program Files)."""
    home = os.path.expanduser("~")
    explicit = [hint, os.environ.get("NFH_DATA")]
    for c in filter(None, explicit):
        for d in (c, os.path.join(c, "data")):
            if _is_data_dir(d):
                return d
    places = [os.getcwd(), os.path.join(home, "Documents"), r"C:\Program Files (x86)\JoWooD",
              r"C:\Program Files (x86)", r"C:\Program Files"]
    for c in places:
        for d in (c, os.path.join(c, "data")):
            if _is_data_dir(d):
                return d
        try:
            for sub in sorted(os.listdir(c)):
                d = os.path.join(c, sub, "data")
                if _is_data_dir(d):
                    return d
        except OSError:
            pass
    return None
