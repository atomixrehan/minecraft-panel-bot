"""Wake-on-join (Java AND Bedrock): while the server is OFFLINE, hold its ports.

Java (TCP 25565)   server-list pings get an "offline – join to start it" status (never starts the server);
                   a real login attempt shows "starting, reconnect in a minute" and starts the server.
Bedrock (UDP 19132, only if Geyser is installed)
                   server-list pings get an "offline" entry; a connection attempt starts the server.
The listeners are closed before the real server starts (it needs the same ports).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import time
from typing import Optional

from .config import DATA_DIR, SERVER_DIR
from .installer import PLUGIN_DIR
from .mcproto import (RAKNET_MAGIC, _packet, _parse_varint, _read_packet, _string, _varint,  # noqa: F401
                      build_pong, raknet_ping)

log = logging.getLogger("panel.wake")
NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")
MAX_CONNECTIONS = 50
COOLDOWN = 20  # seconds between wake-ups
BEDROCK_PONG_FILE = DATA_DIR / "bedrock_pong.txt"   # the real Bedrock status Geyser last sent (so the offline entry looks identical)


def geyser_installed() -> bool:
    return PLUGIN_DIR.exists() and any(p.name.lower().startswith("geyser") for p in PLUGIN_DIR.glob("*.jar"))


def geyser_port() -> int:
    """UDP port Geyser listens on (from its config; default 19132)."""
    try:
        in_bedrock = False
        for line in (PLUGIN_DIR / "Geyser-Spigot" / "config.yml").read_text(encoding="utf-8").splitlines():
            if re.match(r"^bedrock:\s*$", line):
                in_bedrock = True
                continue
            if in_bedrock:
                if line and not line.startswith((" ", "\t", "#")):
                    in_bedrock = False
                    continue
                m = re.match(r"^\s+port:\s*(\d+)", line)
                if m:
                    return int(m.group(1))
    except OSError:
        pass
    return 19132


class _BedrockProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner: "WakeListener"):
        self.owner = owner
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            if not data:
                return
            pid = data[0]
            if pid in (0x01, 0x02) and len(data) >= 25 and data[9:25] == RAKNET_MAGIC:   # server-list ping
                guid, text = self.owner.bedrock_status()
                self.transport.sendto(build_pong(data[1:9], guid, text), addr)
            elif pid == 0x05 and len(data) >= 17 and data[1:17] == RAKNET_MAGIC:         # "open connection request" = a join attempt
                self.owner.bedrock_join()
        except Exception:  # noqa: BLE001
            pass


class WakeListener:
    def __init__(self, bot):
        self.bot = bot
        self.server: Optional[asyncio.AbstractServer] = None
        self.udp: Optional[asyncio.DatagramTransport] = None
        self.error: Optional[str] = None
        self.bedrock_error: Optional[str] = None
        self._lock = asyncio.Lock()
        self._conns = 0
        self._waking = False
        self._last = 0.0
        self._guid = random.getrandbits(62)

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
            if (self.server and (self.udp or not geyser_installed())) or not self.enabled or self.bot.mc.state != "offline":
                return
            if not self.server:
                await self._open_tcp()
            if not self.udp and geyser_installed():
                await self._open_udp()

    async def _open_tcp(self) -> None:
        host = self.bot.mc.properties().get("server-ip") or None
        port = self.bot.mc.port()
        last: Optional[Exception] = None
        for _ in range(10):  # the old JVM may still be releasing the port
            if self.bot.mc.state != "offline":
                return
            try:
                self.server = await asyncio.start_server(self._handle, host, port, reuse_address=True)
                self.error = None
                log.info("Auto-start listener (Java) active on TCP %s", port)
                return
            except OSError as e:
                last = e
                await asyncio.sleep(1)
        self.error = f"could not bind TCP port {port}: {last}"
        log.warning("Auto-start listener %s", self.error)

    async def _open_udp(self) -> None:
        port = geyser_port()
        last: Optional[Exception] = None
        for _ in range(10):
            if self.bot.mc.state != "offline":
                return
            try:
                transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                    lambda: _BedrockProtocol(self), local_addr=("0.0.0.0", port))
                self.udp, self.bedrock_error = transport, None
                log.info("Auto-start listener (Bedrock) active on UDP %s", port)
                return
            except OSError as e:
                last = e
                await asyncio.sleep(1)
        self.bedrock_error = f"could not bind UDP port {port}: {last}"
        log.warning("Auto-start listener (Bedrock) %s", self.bedrock_error)

    async def close(self) -> None:
        async with self._lock:
            srv, self.server = self.server, None
            udp, self.udp = self.udp, None
            if udp:
                udp.close()
                await asyncio.sleep(0.05)   # let the event loop actually release the UDP socket
                log.info("Auto-start listener (Bedrock) closed")
            if srv:
                srv.close()
                try:
                    await asyncio.wait_for(srv.wait_closed(), 5)
                except Exception:  # noqa: BLE001
                    pass
                log.info("Auto-start listener (Java) closed")

    def status_text(self) -> str:
        if not self.enabled:
            return "off"
        parts = []
        if self.server:
            parts.append(f"Java listening on TCP {self.bot.mc.port()}")
        elif self.error:
            parts.append(self.error)
        if geyser_installed():
            if self.udp:
                parts.append(f"Bedrock listening on UDP {geyser_port()}")
            elif self.bedrock_error:
                parts.append(self.bedrock_error)
        return "on – " + ", ".join(parts) if parts else "on – will listen whenever the server is offline"

    # ── Bedrock ─────────────────────────────────────────────
    def bedrock_status(self):
        """-> (server guid, 'MCPE;…' string). Reuses the real status Geyser sent earlier, so the version matches."""
        max_players = self.bot.mc.max_players()
        line1 = "§e§lOffline §r§7– join to start"
        line2 = "Tap to wake the server"
        try:
            parts = BEDROCK_PONG_FILE.read_text(encoding="utf-8").strip().split(";")
        except OSError:
            parts = []
        if len(parts) >= 9 and parts[0] == "MCPE":
            parts[1], parts[4], parts[7] = line1, "0", line2
            try:
                guid = int(parts[6])
            except ValueError:
                guid = self._guid
            return guid, ";".join(parts)
        port = geyser_port()
        return self._guid, f"MCPE;{line1};0;1.0.0;0;{max_players};{self._guid};{line2};Survival;1;{port};{port + 1};"

    def bedrock_join(self) -> None:
        now = time.time()
        if not self._waking and self.bot.mc.state == "offline" and now - self._last >= COOLDOWN:
            self._waking, self._last = True, now
            asyncio.ensure_future(self._wake("a Bedrock player"))

    async def record_bedrock(self) -> None:
        """While online, remember Geyser's real status reply for the offline entry (retries while Geyser boots)."""
        if not geyser_installed():
            return
        for _ in range(8):
            await asyncio.sleep(10)
            if self.bot.mc.state != "online":
                return
            text = await asyncio.to_thread(raknet_ping, "127.0.0.1", geyser_port())
            if text and text.startswith("MCPE"):
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                BEDROCK_PONG_FILE.write_text(text, encoding="utf-8")
                return

    # ── Java connection handling ────────────────────────────
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
