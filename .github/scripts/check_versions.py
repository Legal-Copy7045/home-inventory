"""Check that every place a version is written agrees (and matches a release tag).

Usage: python .github/scripts/check_versions.py [TAG]
Prints the version; exits non-zero on any mismatch.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def grab(path: str, pattern: str) -> str:
    m = re.search(pattern, (ROOT / path).read_text(), re.M)
    if not m:
        sys.exit(f"No version found in {path}")
    return m.group(1)


found = {
    "home_inventory/config.yaml": grab("home_inventory/config.yaml", r'^version:\s*"?([^"\s]+)"?'),
    "home_inventory/server/pyproject.toml": grab("home_inventory/server/pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
    "home_inventory/server/home_inventory_server/__init__.py": grab(
        "home_inventory/server/home_inventory_server/__init__.py", r'^__version__\s*=\s*"([^"]+)"'
    ),
    "home_inventory/frontend/package.json": json.loads((ROOT / "home_inventory/frontend/package.json").read_text())["version"],
    "custom_components/home_inventory/manifest.json": json.loads(
        (ROOT / "custom_components/home_inventory/manifest.json").read_text()
    )["version"],
}
version = found["home_inventory/config.yaml"]
bad = {k: v for k, v in found.items() if v != version}
if bad:
    sys.exit(f"Version mismatch (config.yaml says {version}): {bad}")
if f"## {version}" not in (ROOT / "home_inventory/CHANGELOG.md").read_text():
    sys.exit(f"home_inventory/CHANGELOG.md has no '## {version}' entry")
if not (ROOT / f"docs/release-notes/v{version}.md").exists():
    sys.exit(f"docs/release-notes/v{version}.md is missing (it becomes the GitHub release text)")
if len(sys.argv) > 1 and sys.argv[1] and sys.argv[1].removeprefix("v") != version:
    sys.exit(f"Release tag {sys.argv[1]} does not match version {version} (expected v{version})")
print(version)
