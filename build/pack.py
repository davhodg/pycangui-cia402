# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Pack the plugin as the zip pycangui installs, and keep plugins.json in step.

    python build/pack.py            the zip, in dist/
    python build/pack.py --list     the zip, and plugins.json written for it
    python build/pack.py --check    fail if plugins.json is not for this zip
    python build/pack.py v2.0       the zip, refused unless the tag is VERSION

The same files make the same zip, byte for byte, on any machine: the entries
are sorted, dated 1980, their line endings LF whatever the checkout has, and
stored rather than compressed -- zlib's output differs between builds of it,
which is what Linux and Windows have.
That is what lets plugins.json carry the zip's sha256 before the release
exists, and pycangui refuse a download that does not match it.
"""

import ast
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "cia402"
FOLDER = ROOT / NAME
LISTING = ROOT / "plugins.json"
#: Where a release's zip is, for plugins.json.
RELEASE = "https://github.com/davhodg/pycangui-cia402/releases/download/v{version}/{zip}"
PAGE = "https://github.com/davhodg/pycangui-cia402"
DESCRIPTION = (
    "Drives a CiA 402 motor controller over CANopen or EtherCAT: its state, mode and target, "
    "and what it is doing."
)
WANTED = (".py",)
EPOCH = (1980, 1, 1, 0, 0, 0)


def read_info() -> dict:
    """NAME, VERSION and API_VERSION, read out of plugin.py without running it."""
    tree = ast.parse((FOLDER / "plugin.py").read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node.value.value
    return found


def pack(dest: Path) -> Path:
    files = sorted(
        path
        for path in FOLDER.rglob("*")
        if path.is_file() and path.suffix in WANTED and "__pycache__" not in path.parts
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w") as archive:
        for path in files:
            member = zipfile.ZipInfo(f"{NAME}/{path.relative_to(FOLDER).as_posix()}", EPOCH)
            member.compress_type = zipfile.ZIP_STORED
            member.create_system = 3  # Unix, whatever packs it: it is in the header
            member.external_attr = 0o644 << 16
            archive.writestr(member, path.read_bytes().replace(b"\r\n", b"\n"))
    return dest


def entry(zip_path: Path, info: dict) -> dict:
    version = str(info["VERSION"])
    return {
        "name": NAME,
        "title": info.get("NAME", NAME),
        "description": DESCRIPTION,
        "version": version,
        "api_version": int(info.get("API_VERSION", 1)),
        "url": RELEASE.format(version=version, zip=zip_path.name),
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "page": PAGE,
    }


def main(args: list[str]) -> int:
    info = read_info()
    version = str(info["VERSION"])
    tag = next((a for a in args if not a.startswith("--")), None)
    if tag is not None and tag.removeprefix("v") != version:
        print(f"Tag {tag} is not the plugin's VERSION, {version}.", file=sys.stderr)
        return 1
    zipped = pack(ROOT / "dist" / f"{NAME}-{version}.zip")
    wanted = {"format": 1, "plugins": [entry(zipped, info)]}
    if "--list" in args:
        LISTING.write_text(json.dumps(wanted, indent=2) + "\n", encoding="utf-8")
    if "--check" in args:
        have = json.loads(LISTING.read_text(encoding="utf-8")) if LISTING.is_file() else None
        if have != wanted:
            print(
                "plugins.json is not for this zip: run python build/pack.py --list and commit it.",
                file=sys.stderr,
            )
            return 1
    print(zipped)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
