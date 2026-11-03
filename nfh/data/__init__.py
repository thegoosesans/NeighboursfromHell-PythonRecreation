"""Use `load("level_pig/anims")` to get the list of root `Node`s of that file.

By default the converted copies in this package are used.  `use(Source)` switches to the original
game's own XML (read from gamedata.bnd); a file missing there falls back to the converted copy.
"""

import importlib
from typing import Dict, List, Optional
from ..node import Node

_source = None
_cache: Dict[str, List[Node]] = {}


def use(source) -> None:
    """Read from an `nfh.source.Source` (None = the bundled copies again)."""
    global _source
    _source = source
    _cache.clear()


def load(path: str) -> List[Node]:
    path = path.replace("\\", "/")
    if _source is not None:
        if path not in _cache:
            raw = _source.xml(path)
            if raw is not None:
                from ..xmlio import decode_bytes, parse_text
                _cache[path] = parse_text(decode_bytes(raw))
        if path in _cache:
            return _cache[path]
    mod = importlib.import_module("nfh.data." + path.replace("/", "."))
    return mod.ROOTS
