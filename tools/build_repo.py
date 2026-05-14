#!/usr/bin/env python3
"""Builds addon zips and generates addons.xml + md5 for GitHub Pages hosting."""
import hashlib
import re
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ADDONS_DIR = REPO_ROOT / "addons"
OUT_DIR = REPO_ROOT / "dist" / "repo"

EXCLUDE_DIRS = {".git", ".vscode", "__pycache__", ".pytest_cache"}
EXCLUDE_EXTS = {".pyc", ".pyo", ".zip"}


def get_version(addon_xml: Path) -> str:
    from xml.etree import ElementTree as ET
    return ET.parse(addon_xml).getroot().get("version")


def build_zip(addon_dir: Path) -> Path:
    addon_id = addon_dir.name
    version = get_version(addon_dir / "addon.xml")
    zip_dir = OUT_DIR / addon_id
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"{addon_id}-{version}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(addon_dir.rglob("*")):
            rel = path.relative_to(addon_dir)
            if any(p in EXCLUDE_DIRS for p in rel.parts):
                continue
            if path.suffix in EXCLUDE_EXTS:
                continue
            if path.is_file():
                zf.write(path, Path(addon_id) / rel)

    return zip_path


def build_addons_xml(addon_dirs: list) -> str:
    blocks = []
    for addon_dir in addon_dirs:
        content = (addon_dir / "addon.xml").read_text(encoding="utf-8")
        block = re.sub(r"<\?xml[^?]*\?>\s*", "", content).strip()
        blocks.append(block)

    body = "\n".join(blocks)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<addons>\n{body}\n</addons>\n'


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    addon_dirs = sorted(
        p for p in ADDONS_DIR.iterdir() if p.is_dir() and (p / "addon.xml").exists()
    )

    for addon_dir in addon_dirs:
        zip_path = build_zip(addon_dir)
        print(f"Built {addon_dir.name} v{get_version(addon_dir / 'addon.xml')}")

    addons_xml = build_addons_xml(addon_dirs)
    (OUT_DIR / "addons.xml").write_text(addons_xml, encoding="utf-8")
    md5 = hashlib.md5(addons_xml.encode("utf-8")).hexdigest()
    (OUT_DIR / "addons.xml.md5").write_text(md5)

    print(f"Generated addons.xml ({len(addon_dirs)} addons)")


if __name__ == "__main__":
    main()
