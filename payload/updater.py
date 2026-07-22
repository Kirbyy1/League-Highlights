from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


WAIT_TIMEOUT_SECONDS = 90


def _configure_logging(update_root: Path) -> None:
    log_dir = update_root.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "updater.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Pending update data must be a JSON object.")
    return data


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _wait_for_process(pid: int, timeout_seconds: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return True
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, timeout_seconds * 1000)
            return result == wait_object_0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.25)
    return False


def _preserved_installer_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    preserved: list[Path] = []
    for child in directory.iterdir():
        lower = child.name.lower()
        if lower.startswith("unins") or lower in {"install.log"}:
            preserved.append(child)
    return preserved


def _copy_preserved_files(backup_dir: Path, install_dir: Path, names: list[str]) -> None:
    for name in names:
        source = backup_dir / name
        destination = install_dir / name
        if not source.exists() or destination.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)


def _cleanup_old_backups(backup_root: Path, keep: Path) -> None:
    candidates = sorted(
        (path for path in backup_root.iterdir() if path.is_dir() and path != keep),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in candidates[1:]:
        shutil.rmtree(old, ignore_errors=True)


def _apply_update(pending_path: Path, restart: bool) -> int:
    pending = _read_json(pending_path)
    version = str(pending["version"])
    install_dir = Path(str(pending["install_dir"])).resolve()
    staged_dir = Path(str(pending["staged_dir"])).resolve()
    executable_name = str(pending["executable_name"])
    update_root = pending_path.parent.resolve()
    result_path = update_root / "update_result.json"

    if not install_dir.is_dir():
        raise FileNotFoundError(f"Installation directory not found: {install_dir}")
    if not staged_dir.is_dir():
        raise FileNotFoundError(f"Staged update not found: {staged_dir}")
    if not (staged_dir / executable_name).is_file():
        raise FileNotFoundError(f"Staged executable not found: {executable_name}")
    if install_dir == staged_dir or install_dir in staged_dir.parents:
        raise ValueError("The staged update location is invalid.")

    backup_root = update_root / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_dir = backup_root / f"before-{version}-{int(time.time())}"
    preserved_names = [path.name for path in _preserved_installer_files(install_dir)]
    failed_new_dir: Path | None = None

    logging.info("Applying update %s from %s to %s", version, staged_dir, install_dir)
    try:
        os.replace(install_dir, backup_dir)
        try:
            shutil.move(str(staged_dir), str(install_dir))
            _copy_preserved_files(backup_dir, install_dir, preserved_names)
        except Exception:
            if install_dir.exists():
                failed_new_dir = update_root / f"failed-{version}-{int(time.time())}"
                shutil.move(str(install_dir), str(failed_new_dir))
            os.replace(backup_dir, install_dir)
            raise

        if restart:
            executable = install_dir / executable_name
            try:
                subprocess.Popen(
                    [str(executable), "--post-update", version],
                    cwd=str(install_dir),
                    close_fds=True,
                )
            except Exception:
                failed_new_dir = update_root / f"failed-launch-{version}-{int(time.time())}"
                shutil.move(str(install_dir), str(failed_new_dir))
                os.replace(backup_dir, install_dir)
                raise

        pending_path.unlink(missing_ok=True)
        _write_json_atomic(
            result_path,
            {
                "success": True,
                "version": version,
                "backup_dir": str(backup_dir),
                "installed_at": int(time.time()),
            },
        )
        _cleanup_old_backups(backup_root, backup_dir)
        logging.info("Update %s installed successfully", version)
        return 0
    except Exception as exc:
        logging.exception("Update %s failed", version)
        _write_json_atomic(
            result_path,
            {
                "success": False,
                "version": version,
                "error": str(exc),
                "failed_dir": str(failed_new_dir) if failed_new_dir else "",
                "failed_at": int(time.time()),
            },
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="League Highlights external updater")
    parser.add_argument("--pending", required=True, type=Path)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    pending_path = args.pending.resolve()
    _configure_logging(pending_path.parent)
    logging.info("Updater helper started; waiting for PID %s", args.pid)
    if not _wait_for_process(args.pid, WAIT_TIMEOUT_SECONDS):
        logging.error("League Highlights did not exit within %s seconds", WAIT_TIMEOUT_SECONDS)
        return 2
    time.sleep(0.45)  # allow antivirus and Qt multimedia handles to settle
    return _apply_update(pending_path, bool(args.restart))


if __name__ == "__main__":
    raise SystemExit(main())
