"""Wake-on-join: while the server is OFFLINE, listen on the Minecraft port.

* Server-list pings get a friendly "offline – join to start it" status (never starts the server).
* A real login attempt shows a "starting, reconnect in a minute" message and starts the server.
The listener is closed before the real server starts (it needs the same port).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Optional

from . import props
from .config import SERVER_DIR

log = logging.getLogger("panel.wake")
NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
MAX_CONNECTIONS = 50
COOLDOWN = 20  # seconds between wake-ups


# ── minimal Minecraft (Java) protocol helpers ──────────────────
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def _parse_varint(data: bytes, off: int):
    result = 0
    for i in range(5):
        b = data[off]
        off += 1
        result |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return result, off
    raise ValueError("varint too long")


def _string(s: str) -> bytes:
    d = s.encode("utf-8")
    return _varint(len(d)) + d


def _packet(pid: int, payload: bytes = b"") -> bytes:
    body = _varint(pid) + payload
    return _varint(len(body)) + body


async def _read_varint(reader: asyncio.StreamReader) -> int:
    result = 0
    for i in range(5):
        b = (await reader.readexactly(1))[0]
        result |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return result
    raise ValueError("varint too long")


async def _read_packet(reader: asyncio.StreamReader):
    length = await _read_varint(reader)
    if length <= 0 or length > 4096:
        raise ValueError("bad packet length")
    data = await reader.readexactly(length)
    pid, off = _parse_varint(data, 0)
    return pid, data[off:]


class WakeListener:
    def __init__(self, bot):
        self.bot = bot
        self.server: Optional[asyncio.AbstractServer] = None
        self.error: Optional[str] = None
        self._lock = asyncio.Lock()
        self._conns = 0
        self._waking = False
        self._last = 0.0

    # ── lifecycle ───────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return bool(self.bot.cfg["autostart"])

    async def sync(self) -> None:
        """Listen iff enabled and the server is offline."""
        if self.enabled and self.bot.mc.state == "offline":
            await self.open()
        else:
            await self.close()

    async def open(self) -> None:
        async with self._lock:
            if self.server or not self.enabled or self.bot.mc.state != "offline":
                return
            p = self.bot.mc.properties()
            host = p.get("server-ip") or None
            port = self.bot.mc.port()
            last: Optional[Exception] = None
            for _ in range(10):  # the old JVM may still be releasing the port
                if self.bot.mc.state != "offline":
                    return
                try:
                    self.server = await asyncio.start_server(self._handle, host, port, reuse_address=True)
                    self.error = None
                    log.info("Auto-start listener active on port %s", port)
                    return
                except OSError as e:
                    last = e
                    await asyncio.sleep(1)
            self.error = f"could not bind port {port}: {last}"
            log.warning("Auto-start listener %s", self.error)

    async def close(self) -> None:
        async with self._lock:
            srv, self.server = self.server, None
            if srv:
                srv.close()
                try:
                    await asyncio.wait_for(srv.wait_closed(), 5)
                except Exception:  # noqa: BLE001
                    pass
                log.info("Auto-start listener closed")

    def status_text(self) -> str:
        if not self.enabled:
            return "off"
        if self.server:
            return f"on – listening on port {self.bot.mc.port()} while the server is offline"
        if self.error:
            return f"on, but {self.error}"
        return "on – will listen whenever the server is offline"

    # ── connection handling ─────────────────────────────────
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._conns >= MAX_CONNECTIONS:
            writer.close()
            return
        self._conns += 1
        try:
            await asyncio.wait_for(self._serve(reader, writer), 10)
        except Exception:  # noqa: BLE001  (timeouts, scanners, legacy pings…)
            pass
        finally:
            self._conns -= 1
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _serve(self, reader, writer) -> None:
        pid, payload = await _read_packet(reader)
        if pid != 0:
            return
        proto, off = _parse_varint(payload, 0)
        alen, off = _parse_varint(payload, off)
        off += alen + 2
        state, _ = _parse_varint(payload, off)
        if state == 1:  # server list
            while True:
                pid, payload = await _read_packet(reader)
                if pid == 0:
                    writer.write(_packet(0, _string(json.dumps(self._status(proto)))))
                    await writer.drain()
                elif pid == 1:
                    writer.write(_packet(1, payload[:8]))
                    await writer.drain()
                    return
                else:
                    return
        elif state in (2, 3):  # login / transfer
            name = None
            try:
                pid, payload = await _read_packet(reader)
                if pid == 0:
                    nlen, off = _parse_varint(payload, 0)
                    name = payload[off:off + nlen].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            await self._login(name, writer)

    def _kick(self, writer, text: str, sub: str = "", colour: str = "green") -> None:
        msg = {"text": text, "color": colour}
        if sub:
            msg["extra"] = [{"text": "\n" + sub, "color": "gray"}]
        writer.write(_packet(0, _string(json.dumps(msg))))

    def _whitelisted(self, name: str) -> bool:
        if self.bot.mc.properties().get("white-list", "false") != "true":
            return True
        try:
            data = json.loads((SERVER_DIR / "whitelist.json").read_text(encoding="utf-8"))
            return name.lower() in {e.get("name", "").lower() for e in data}
        except Exception:  # noqa: BLE001
            return False

    async def _login(self, name: Optional[str], writer) -> None:
        if not name or not NAME_RE.match(name):
            self._kick(writer, "Invalid username.", colour="red")
        elif not self._whitelisted(name):
            self._kick(writer, "You are not whitelisted on this server!", colour="red")
        else:
            self._kick(writer, "The server was asleep – it is starting now!", "Please reconnect in about a minute.")
            now = time.time()
            if not self._waking and self.bot.mc.state == "offline" and now - self._last >= COOLDOWN:
                self._waking, self._last = True, now
                asyncio.ensure_future(self._wake(name))
        try:
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass

    async def _wake(self, name: str) -> None:
        try:
            await asyncio.sleep(0.5)  # let the player's connection finish first
            await self.close()
            await self.bot.send_console(f"🔔 **{name}** tried to join while the server was offline – starting it now.")
            ok, msg = await self.bot.mc.start()
            if not ok:
                await self.bot.send_console(f"⚠️ Auto-start failed: {msg}")
                if self.bot.mc.state == "offline":
                    await self.open()
        except Exception:  # noqa: BLE001
            log.exception("auto-start failed")
            if self.bot.mc.state == "offline":
                await self.open()
        finally:
            self._waking = False

    def _status(self, proto: int) -> dict:
        p = self.bot.mc.properties()
        motd = p.get("motd", "A Minecraft Server").replace("\\n", "\n").split("\n")[0]
        status = {
            "version": {"name": "Offline", "protocol": proto},
            "players": {"max": self.bot.mc.max_players(), "online": 0},
            "description": {"text": f"{motd}\n§e§lOffline §r§7– join to start the server!"},
        }
        icon = SERVER_DIR / "server-icon.png"
        if icon.is_file() and icon.stat().st_size < 60000:
            status["favicon"] = "data:image/png;base64," + base64.b64encode(icon.read_bytes()).decode()
        return status
