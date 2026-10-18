from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"
SERVER_DIR = ROOT / "server"
RUNTIME_DIR = ROOT / "runtime"
BACKUP_DIR = ROOT / "backups"
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
DEFAULT_ICON = ASSETS_DIR / "icon.png"

DEFAULTS: dict = {
    "token": "",
    "op_role_id": 0,
    "panel_channel_id": 0,
    "crosschat_channel_id": 0,
    "console_channel_id": 0,
    "configure_channel_id": 0,
    "panel_message_id": 0,
    "software": "paper",
    "mc_version": "",
    "ram_mb": 2048,
    "eula": False,
    "setup_done": False,
    "setup_steps": [],             # wizard steps already answered (so an interrupted setup RESUMES instead of starting over)
    "install_done": False,         # server jar + chosen plugins fully installed
    "plugins": [],                 # plugin keys picked in the wizard (kept so a repair can re-install them)
    "autoshut": {"enabled": True, "minutes": 3},
    "autostart": True,             # start the server when someone tries to join while it is offline
    "autorestart_hours": 0,        # 0 = off
    "backup_hours": 0,             # 0 = off
    "max_backups": 10,
    "last_backup": 0,
    "gamerules": {},               # applied every time the server finishes starting
}

ID_KEYS = ("op_role_id", "panel_channel_id", "crosschat_channel_id",
           "console_channel_id", "configure_channel_id")


def _merge(base: dict, new: dict) -> None:
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


class Config:
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path
        self.data: dict = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            _merge(self.data, raw)
            if raw.get("setup_done"):  # config from an older version: everything was already answered/installed
                if "setup_steps" not in raw:
                    self.data["setup_steps"] = ["*"]
                if "install_done" not in raw:
                    self.data["install_done"] = True

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        try:
            os.chmod(tmp, 0o600)  # token lives here
        except OSError:
            pass
        os.replace(tmp, self.path)

    def is_configured(self) -> bool:
        return bool(self.data["token"]) and all(self.data[k] for k in ID_KEYS) and bool(self.data["setup_done"])

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


def parse_ram(text: str) -> int:
    """'4G' / '4096M' / '4' -> megabytes."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(g|gb|m|mb)?", text.strip().lower().replace(" ", ""))
    if not m:
        raise ValueError("Use a format like 4G, 4096M or 4")
    n = float(m.group(1))
    unit = m.group(2) or ("m" if n >= 256 else "g")
    mb = int(n * 1024) if unit.startswith("g") else int(n)
    if mb < 512:
        raise ValueError("Minimum is 512 MB")
    return mb
