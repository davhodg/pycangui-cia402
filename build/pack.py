# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Pack the plugin as the zip pycangui installs: ``python build/pack.py [tag]``.

Packed by pycangui's own code, so the zip is exactly what *Plugins > Install
from file...* expects. Given a tag (``v2.0``), refuses one that is not the
plugin's VERSION, so a release cannot carry a zip that says otherwise.
"""

import sys
from pathlib import Path

from pycangui.core.plugin_package import pack
from pycangui.core.plugins import describe

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "cia402"


def main() -> int:
    version = describe(FOLDER / "plugin.py").version
    if len(sys.argv) > 1 and sys.argv[1].removeprefix("v") != version:
        print(f"Tag {sys.argv[1]} is not the plugin's VERSION, {version}.", file=sys.stderr)
        return 1
    print(pack(FOLDER, ROOT / "dist" / f"cia402-{version}.zip"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
