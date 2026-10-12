from __future__ import annotations

import os
import shutil
import time
import zipfile
from pathlib import Path

from . import props
from .config import BACKUP_DIR, SERVER_DIR

EXTRA_FILES = ["server.properties", "whitelist.json", "ops.json", "banned-players.json", "banned-ips.json"]


def level_name() -> str:
    return props.read(SERVER_DIR / "server.properties").get("level-name", "world")


def world_dirs() -> list:
    lvl = level_name()
    return [SERVER_DIR / n for n in (lvl, f"{lvl}_nether", f"{lvl}_the_end") if (SERVER_DIR / n).is_dir()]


def create_zip(prefix: str = "backup") -> Path:
    worlds = world_dirs()
    if not worlds:
        raise RuntimeError("No world found yet – start the server once first.")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    final = BACKUP_DIR / f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    tmp = final.with_name(final.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for d in worlds:
            for root, _, files in os.walk(d):
                for f in files:
                    if f == "session.lock":
                        continue
                    p = Path(root) / f
                    try:
                        z.write(p, str(p.relative_to(SERVER_DIR)))
                    except OSError:
                        continue  # file vanished/locked mid-backup
        for n in EXTRA_FILES:
            if (SERVER_DIR / n).is_file():
                z.write(SERVER_DIR / n, n)
    os.replace(tmp, final)
    return final


def list_backups() -> list:
    if not BACKUP_DIR.exists():
        return []
    items = [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime} for p in BACKUP_DIR.glob("*.zip")]
    return sorted(items, key=lambda x: x["mtime"], reverse=True)


def prune(keep: int) -> None:
    auto = [b for b in list_backups() if b["name"].startswith("backup-")]
    for b in auto[max(keep, 1):]:
        (BACKUP_DIR / b["name"]).unlink(missing_ok=True)


def _safe(name: str) -> Path:
    if Path(name).name != name or not name.endswith(".zip"):
        raise ValueError("Invalid backup name")
    p = BACKUP_DIR / name
    if not p.is_file():
        raise FileNotFoundError("Backup not found")
    return p


def delete_backup(name: str) -> None:
    _safe(name).unlink()


def delete_worlds() -> None:
    for d in world_dirs():
        shutil.rmtree(d)


def restore(name: str) -> None:
    zp = _safe(name)
    root = SERVER_DIR.resolve()
    with zipfile.ZipFile(zp) as z:
        members = [m for m in z.namelist() if m != "server.properties"]  # keep current settings
        for m in members:  # zip-slip guard
            if not str((root / m).resolve()).startswith(str(root) + os.sep):
                raise ValueError("Unsafe path in backup archive")
        delete_worlds()
        z.extractall(SERVER_DIR, members=members)


def import_world(zip_path) -> str:
    """Replace the current world with the contents of a world .zip. Returns a short summary."""
    root = SERVER_DIR.resolve()
    lvl = level_name()
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        if sum(i.file_size for i in infos) > 30 * 1024 ** 3:
            raise ValueError("Archive is too large (over 30 GB uncompressed).")
        levels = [i.filename for i in infos if not i.is_dir() and Path(i.filename).name == "level.dat"]
        if not levels:
            raise ValueError("No level.dat found – that doesn't look like a Minecraft world zip.")
        best = min(levels, key=lambda n: n.count("/"))
        prefix = best[:-len("level.dat")]                 # "" or "MyWorld/"
        mapping = [(prefix, lvl)]
        if prefix:                                         # Bukkit-style sibling dimension folders
            base = prefix.rstrip("/").split("/")[-1]
            parent = prefix[: -(len(base) + 1)]
            mapping += [(f"{parent}{base}_nether/", f"{lvl}_nether"), (f"{parent}{base}_the_end/", f"{lvl}_the_end")]
        # validate every target path first (zip-slip guard) before touching the old world
        plan = []
        for info in infos:
            for src, dst in mapping:
                if info.filename.startswith(src):
                    rel = info.filename[len(src):]
                    if rel and Path(rel).name != "session.lock":
                        target = (SERVER_DIR / dst / rel).resolve()
                        if not str(target).startswith(str(root) + os.sep):
                            raise ValueError("Unsafe path in archive.")
                        plan.append((info, target))
                    break
        delete_worlds()
        for info, target in plan:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src_f, open(target, "wb") as out:
                    shutil.copyfileobj(src_f, out)
    return f"{len(plan)} files imported into `{lvl}`"
