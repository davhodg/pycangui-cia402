# pycangui-cia402

CANopen motor control (CiA 402) for [pycangui](https://github.com/davhodg/pycangui): a
plugin that drives a motor controller -- its state, its mode, its target and what it is
actually doing.

The modes, targets and actual values are ordinary objects, which a custom pane could show.
What needs a plugin is the state machine: the walk to *Operation enabled* is a sequence of
writes where each depends on what the drive answered to the last, and the state is decoded
from overlapping masks of the statusword. [docs/cia402.md](docs/cia402.md) is the whole
of what it does.

**It moves real motors.** It asks before enabling a drive, halts it when its pane is put
away or pycangui closes, and says loudly when a motor is live -- but what a drive does
when enabled is the drive's and its setup's. Try it on a bench first.

## Installing

It needs pycangui with plugin API 3, which is in the release after 0.6.2; until then,
pycangui from master.

In pycangui, *Plugins > Manage plugins...*, *Check for plugins*, then choose it and
*Install*. pycangui finds it through this repository's [plugins.json](plugins.json),
which its own plugin list includes.

Or download `cia402-<version>.zip` from [Releases](../../releases) and use *Plugins >
Install plugin...*.

pycangui 0.6 and earlier ship an older version of this plugin, under *Plugins > Supplied
with pycangui*.

## Developing

The plugin is the `cia402` folder, as pycangui loads it; it imports nothing of pycangui's,
only what the `app` it is handed offers. The tests run it inside a pycangui window, so
pycangui is a test dependency.

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -n auto
```

To try an edit in pycangui, copy or link the `cia402` folder into the workspace's plugins
folder (*Plugins > Open plugins folder*) and use *Plugins > Reload plugins*.

## Releasing

1. Set `VERSION` in `cia402/plugin.py`.
2. `python build/pack.py --list` writes `plugins.json` for the new zip: its version, URL
   and sha256. The zip is the same byte for byte on any machine, so the sha256 is known
   before the release exists. CI fails while `plugins.json` does not match.
3. Commit both, and push a tag `v<VERSION>`. CI tests it and makes a GitHub release with
   the zip, which is the file `plugins.json` points to.

## Licence

Apache-2.0, as pycangui: see [LICENSE](LICENSE).

CANopen® and CiA® are registered trademarks of CAN in Automation e.V. This project is not
affiliated with CAN in Automation.
