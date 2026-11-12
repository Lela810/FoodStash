"""Verify all release surfaces; optionally require an exact stable release tag."""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def check_release(root=ROOT, tag=None):
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    errors = []
    if not re.fullmatch(SEMVER, version):
        errors.append("VERSION must be a stable X.Y.Z semantic version (no leading zeroes).")
    if tag is not None and (not re.fullmatch("v" + SEMVER, tag) or tag != f"v{version}"):
        errors.append(f"Release tag must be exactly v{version}.")
    readme = (root / "README.md").read_text(encoding="utf-8")
    if re.findall(r"^Current release: \*\*v([^*]+)\*\*$", readme, re.M) != [version]:
        errors.append("README current release must match VERSION exactly.")
    image_versions = re.findall(r"ghcr\.io/lela810/foodstash:([0-9]+\.[0-9]+\.[0-9]+)", readme)
    if not image_versions or any(value != version for value in image_versions):
        errors.append("README image examples must match VERSION.")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    if f"ghcr.io/lela810/foodstash:${{FOODSTASH_VERSION:-{version}}}" not in compose:
        errors.append("Compose default image version must match VERSION.")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## ([0-9]+\.[0-9]+\.[0-9]+) - \d{4}-\d{2}-\d{2}$", changelog, re.M)
    if not headings or headings[0] != version:
        errors.append("First CHANGELOG release must match VERSION and include YYYY-MM-DD.")
    return version, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Tag that must match VERSION, e.g. v0.1.0")
    args = parser.parse_args()
    version, errors = check_release(tag=args.tag)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Release metadata consistent: v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
