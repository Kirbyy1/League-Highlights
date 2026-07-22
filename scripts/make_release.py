from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.release_notes import notes_for_version  # noqa: E402
from app.version import APP_VERSION, REPOSITORY_SLUG  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_zip(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            bundle.write(path, relative)


def main() -> int:
    dist = ROOT / "dist" / "LeagueHighlights"
    if not (dist / "LeagueHighlights.exe").is_file():
        raise SystemExit("Build dist/LeagueHighlights first.")
    if not (dist / "LeagueHighlightsUpdater.exe").is_file():
        raise SystemExit("LeagueHighlightsUpdater.exe is missing from the app build.")

    release_dir = ROOT / "release" / APP_VERSION
    release_dir.mkdir(parents=True, exist_ok=True)
    package_name = f"LeagueHighlights-{APP_VERSION}.zip"
    package_path = release_dir / package_name
    build_zip(dist, package_path)

    digest = sha256_file(package_path)
    package_size = package_path.stat().st_size
    tag = f"v{APP_VERSION}"
    package_url = (
        f"https://github.com/{REPOSITORY_SLUG}/releases/download/{tag}/{package_name}"
    )
    manifest = {
        "schema": 1,
        "version": APP_VERSION,
        "channel": "stable",
        "package": {
            "url": package_url,
            "sha256": digest,
            "size": package_size,
        },
        "release_notes": notes_for_version(APP_VERSION),
    }
    (release_dir / "update.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (release_dir / "SHA256SUMS.txt").write_text(
        f"{digest}  {package_name}{os.linesep}", encoding="utf-8"
    )
    print(f"Release files created in {release_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
