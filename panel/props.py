"""Minimal reader/writer for Minecraft's server.properties (keeps comments)."""
from __future__ import annotations

import re
from pathlib import Path


def _unescape(v: str) -> str:
    v = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), v)
    try:  # re-join surrogate pairs (emoji)
        v = v.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        pass
    return v.replace("\\\\", "\\")


def _escape(v: str) -> str:
    out = []
    for ch in v.replace("\\", "\\\\"):
        o = ord(ch)
        if o < 128:
            out.append(ch)
        elif o < 0x10000:
            out.append("\\u%04x" % o)
        else:
            o -= 0x10000
            out.append("\\u%04x\\u%04x" % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
    return "".join(out)


def read(path: Path) -> dict:
    data: dict = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        data[k.strip()] = _unescape(v)
    return data


def write_key(path: Path, key: str, value, raw: bool = False) -> None:
    if isinstance(value, bool):
        value = "true" if value else "false"
    if not raw:
        value = _escape(str(value).replace("\n", " ").replace("\r", " "))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def motd_value(text: str) -> str:
    """User text -> escaped server.properties MOTD. '&a' = colour codes, a literal \\n = new line."""
    parts = [_escape(part.replace("&", "\u00a7")) for part in text.split("\\n")]
    return "\\n".join(parts)
