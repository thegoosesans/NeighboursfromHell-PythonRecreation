#!/usr/bin/env python3
"""Compare the original game's data with the converted copies in nfh/data and check the sprites.

    python tools/verify_original.py <folder with gamedata.bnd>

Reports XML files that differ from (or are missing in) the bundled copies, and sprites / sounds the
original XML references that cannot be found in gfxdata.bnd / sfxdata.bnd.
"""
from __future__ import annotations
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nfh import data, level_names, load_level          # noqa: E402
from nfh.assets import SoundBank, SpriteBank            # noqa: E402
from nfh.source import Source, find_data_dir            # noqa: E402
from nfh.xmlio import decode_bytes, parse_text          # noqa: E402


def main():
    d = find_data_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    if not d:
        sys.exit("no gamedata.bnd found; pass the game's data folder")
    src = Source(d)
    same, diff, nocopy = 0, [], []
    for key, n in sorted(src.gamedata._names.items()):          # n keeps the archive's own spelling
        if not n.endswith(".xml"):
            continue
        path = n[:-4]
        try:
            mod = importlib.import_module("nfh.data." + path.replace("/", "."))
        except ImportError:
            nocopy.append(path)
            continue
        a = [r.as_tuple() for r in parse_text(decode_bytes(src.gamedata.read(key)))]
        if a == [r.as_tuple() for r in mod.ROOTS]:
            same += 1
        else:
            diff.append(path)
    print(f"data folder: {d}\nXML identical to the bundled copy: {same}")
    print(f"differs ({len(diff)}):", ", ".join(diff) or "-")
    print(f"no bundled copy ({len(nocopy)}):", ", ".join(nocopy) or "-")

    data.use(src)
    bank, snd = SpriteBank(None, src.gfx), SoundBank(src.sfx)
    bad = 0
    for name in level_names():
        L = load_level(name)
        ms, mw = bank.missing(L), snd.missing(f for f, _ in L.sfx)
        bad += len(ms) + len(mw)
        if ms or mw:
            print(f"  {name}: {len(ms)} sprites, {len(mw)} sounds missing", ms[:3], mw[:3])
    print(f"levels: {len(level_names())}, missing sprites+sounds: {bad}")


if __name__ == "__main__":
    main()
