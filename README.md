# NeighboursfromHell-PythonRecreation
This is fan-made recreation of a 2003 PC game called Neighbours from Hell. Keep in mind it's still WIP.


## Playing with the original game's data

The viewer can read an installed copy of the game directly, no unpacking needed:

```
python play.py --game "<folder with gamedata.bnd>"
```

`--game` takes the game's `data` folder (or the folder above it). The `.bnd` files are plain ZIP archives: XML comes
from `gamedata.bnd`, sprites from `gfxdata.bnd` (including its RLE-compressed `.tga` files) and sounds from
`sfxdatahigh.bnd`/`sfxdata.bnd`. Without `--game` the viewer looks in `NFH_DATA`, the current folder and
`Documents/*/data`; `--no-original` turns that off and uses the bundled copies of the XML.

`python tools/verify_original.py <data folder>` lists XML that differs from the bundled copies and any sprites or
sounds the original XML refers to that are missing. If the data's `leveldata.xml` lists fewer levels than the data
folder holds, the others are still offered.
