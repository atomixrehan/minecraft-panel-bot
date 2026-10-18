"""Runs the Minecraft server as a child process and parses its console."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import psutil

from . import backups, installer, mcproto, props
from .config import SERVER_DIR

log = logging.getLogger("panel.server")

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PFX = r"^\[[\d:]+(?: INFO\]|\] \[[^\]]*/INFO\]): "
# Several independent signals, so a plugin that rewrites the join/leave MESSAGE can't fool us:
JOIN_RE = re.compile(PFX + r"(\S+) joined the game$")
LOGIN_RE = re.compile(PFX + r"(\S+?)\[/[^\]]*\] logged in with entity id")     # vanilla, always logged
LEAVE_RE = re.compile(PFX + r"(\S+) left the game$")
LOST_RE = re.compile(PFX + r"(\S+) lost connection: ")                          # vanilla, always logged
LIST_PATTERNS = [re.compile(r"There are (\d+)\s*(?:of a max of|out of maximum|out of|of)\s*(\d+)", re.I),
                 re.compile(r"(\d+)\s*/\s*(\d+)\s+players", re.I)]
PFX_RE = re.compile(PFX)

# Aikar-style G1GC flags (safe subset that works on Java 11-25)
JAVA_FLAGS = [
    "-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=200",
    "-XX:+UnlockExperimentalVMOptions", "-XX:+DisableExplicitGC", "-XX:+PerfDisableSharedMem",
    "-XX:G1NewSizePercent=30", "-XX:G1MaxNewSizePercent=40", "-XX:G1HeapRegionSize=8M",
    "-XX:G1ReservePercent=20", "-XX:InitiatingHeapOccupancyPercent=15", "-XX:SurvivorRatio=32",
    "-XX:MaxTenuringThreshold=1", "-Dusing.aikars.flags=https://mcflags.emc.gs", "-Daikars.new.flags=true",
]


class ServerManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.state = "offline"            # offline | starting | online | stopping
        self.players: set = set()
        self.online_at: Optional[float] = None
        self.empty_since: Optional[float] = None
        self.exit_code: Optional[int] = None
        self.count_hint: Optional[int] = None   # last count reported by the server itself (status ping)
        self.stopping = False
        self.callbacks: dict = defaultdict(list)
        self.pre_start: list = []          # async hooks awaited before the JVM is launched (free the port)
        self._tasks: set = set()
        self._lock = asyncio.Lock()
        self._backup_lock = asyncio.Lock()
        self._save_evt: Optional[asyncio.Event] = None
        self._captures: list = []
        self._ps: Optional[psutil.Process] = None

    # ── events ──────────────────────────────────────────────
    def on(self, name: str, cb) -> None:
        self.callbacks[name].append(cb)

    def _fire(self, name: str, *args) -> None:
        for cb in self.callbacks[name]:
            try:
                res = cb(*args)
                if asyncio.iscoroutine(res):
                    t = asyncio.ensure_future(res)
                    self._tasks.add(t)
                    t.add_done_callback(self._tasks.discard)
            except Exception:  # noqa: BLE001
                log.exception("callback for %s failed", name)

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self._fire("state", state)

    # ── server.properties helpers ───────────────────────────
    def properties(self) -> dict:
        return props.read(SERVER_DIR / "server.properties")

    def max_players(self) -> int:
        try:
            return int(self.properties().get("max-players", "20"))
        except ValueError:
            return 20

    def port(self) -> int:
        try:
            return int(self.properties().get("server-port", "25565"))
        except ValueError:
            return 25565

    # ── lifecycle ───────────────────────────────────────────
    async def start(self):
        async with self._lock:
            if self.state != "offline":
                return False, f"Server is already {self.state}."
            if not (SERVER_DIR / "server.jar").exists():
                return False, "server.jar is missing – run `./start.sh --setup`."
            self._set_state("starting")
        try:
            for hook in self.pre_start:
                await hook()
            await asyncio.to_thread(installer.patch_configs, self.cfg)  # DiscordSRV token/channel, Geyser auth
            java = await asyncio.to_thread(installer.ensure_java, installer.required_java(self.cfg["mc_version"] or "1.21"), log.info)
            ram = int(self.cfg["ram_mb"])
            cmd = [java, f"-Xms{ram}M", f"-Xmx{ram}M", *JAVA_FLAGS, "-jar", "server.jar", "nogui"]
            self.proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(SERVER_DIR), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=1 << 20)
        except Exception as e:  # noqa: BLE001
            self._set_state("offline")
            log.exception("start failed")
            return False, f"Failed to start: {e}"
        self.players.clear()
        self.stopping = False
        self.exit_code = None
        self.online_at = None
        self.empty_since = None
        try:
            self._ps = psutil.Process(self.proc.pid)
            self._ps.cpu_percent(None)
        except Exception:  # noqa: BLE001
            self._ps = None
        asyncio.ensure_future(self._read_loop(self.proc))
        return True, "Server is starting…"

    async def _read_loop(self, proc) -> None:
        try:
            while True:
                try:
                    raw = await proc.stdout.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    continue
                if not raw:
                    break
                line = ANSI.sub("", raw.decode("utf-8", "replace")).rstrip()
                if line:
                    self._handle(line)
        except Exception:  # noqa: BLE001
            log.exception("reader crashed")
        code = await proc.wait()
        crashed = (not self.stopping) and code != 0
        self.exit_code = code
        self.proc = None
        self._ps = None
        self.players.clear()
        self.count_hint = None
        self.online_at = None
        self.empty_since = None
        self._set_state("offline")
        self._fire("exit", code, crashed)

    def _handle(self, line: str) -> None:
        self._fire("line", line)
        for cap in self._captures:
            cap.append(line)
        if self._save_evt and "Saved the game" in line:
            self._save_evt.set()
        m = JOIN_RE.match(line) or LOGIN_RE.match(line)
        if m:
            is_new = m.group(1) not in self.players
            self.players.add(m.group(1))
            self.empty_since = None
            self.count_hint = None
            if is_new:
                self._fire("join", m.group(1))
            return
        m = LEAVE_RE.match(line) or LOST_RE.match(line)
        if m:
            was_in = m.group(1) in self.players
            self.players.discard(m.group(1))
            self.count_hint = None
            if not self.players and self.state == "online":
                self.empty_since = time.time()
            if was_in:
                self._fire("leave", m.group(1))
            return
        if self.state == "starting" and "Done (" in line and "For help" in line:
            self.online_at = self.empty_since = time.time()
            self._set_state("online")
            t = asyncio.ensure_future(self._post_start())
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        elif self.state in ("starting", "online") and re.search(r"Stopping (the )?server", line):
            self._set_state("stopping")

    async def _post_start(self) -> None:
        await asyncio.sleep(1)
        for rule, val in dict(self.cfg["gamerules"]).items():
            try:
                await self.send(f"gamerule {rule} {str(val).lower()}")
            except Exception:  # noqa: BLE001
                break
        if await asyncio.to_thread(installer.patch_configs, self.cfg):
            self._fire("line", "[panel] Plugin configs were updated - restart the server once to apply them.")

    async def send(self, command: str) -> None:
        if not self.proc or not self.proc.stdin or self.proc.stdin.is_closing():
            raise RuntimeError("Server is not running.")
        self.proc.stdin.write((command.replace("\n", " ").replace("\r", " ") + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def run(self, command: str, wait: float = 1.5) -> list:
        """Send a command and return console lines seen shortly afterwards."""
        cap: list = []
        self._captures.append(cap)
        try:
            await self.send(command)
            await asyncio.sleep(wait)
        finally:
            self._captures.remove(cap)
        return [PFX_RE.sub("", l) for l in cap]

    async def wait_offline(self, timeout: float = 30) -> bool:
        end = time.time() + timeout
        while self.state != "offline" and time.time() < end:
            await asyncio.sleep(0.5)
        return self.state == "offline"

    async def stop(self):
        if self.state == "offline":
            return False, "Server is already offline."
        if self.state == "stopping":
            return False, "Server is already stopping."
        proc = self.proc
        if proc is None:
            return False, "Server is still preparing – try again in a few seconds."
        self.stopping = True
        self._set_state("stopping")
        try:
            await self.send("stop")
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(proc.wait(), 90)
        except asyncio.TimeoutError:
            log.warning("server did not stop in 90s – killing")
            proc.kill()
        await self.wait_offline(15)
        return True, "Server stopped."

    async def kill(self):
        if not self.proc:
            return False, "Server is not running."
        self.stopping = True
        self.proc.kill()
        await self.wait_offline(15)
        return True, "Server process killed."

    async def restart(self):
        if self.state == "offline":
            return await self.start()
        ok, msg = await self.stop()
        if not ok:
            return ok, msg
        return await self.start()

    async def shutdown(self) -> None:
        if self.proc is not None and self.state != "offline":
            await self.stop()

    # ── asking the server itself how many players are on ─────
    def player_total(self) -> int:
        """Best known number of players (console tracking AND what the server last reported)."""
        return max(len(self.players), self.count_hint or 0)

    async def query_online(self) -> Optional[int]:
        """Players online according to the server's own status reply (None = couldn't ask)."""
        host = self.properties().get("server-ip") or "127.0.0.1"
        st = await mcproto.status_ping(host, self.port())
        try:
            return int(st["players"]["online"]) if st else None
        except (KeyError, TypeError, ValueError):
            return None

    async def count_players(self) -> Optional[int]:
        """Authoritative count: status ping, then the `list` command as a second opinion. None = unknown."""
        counts = []
        n = await self.query_online()
        if n is not None:
            counts.append(n)
        if not n:  # None or 0 -> also ask the console (covers hide-online-players and odd status replies)
            try:
                for line in await self.run("list", 1.5):
                    hit = next((p.search(line) for p in LIST_PATTERNS if p.search(line)), None)
                    if hit:
                        counts.append(int(hit.group(1)))
                        break
            except Exception:  # noqa: BLE001
                pass
        return max(counts) if counts else None

    # ── backups ─────────────────────────────────────────────
    async def backup(self, prefix: str = "backup") -> Path:
        async with self._backup_lock:
            online = self.state == "online"
            if online:
                await self.send("save-off")
                self._save_evt = asyncio.Event()
                await self.send("save-all flush")
                try:
                    await asyncio.wait_for(self._save_evt.wait(), 90)
                except asyncio.TimeoutError:
                    pass
                self._save_evt = None
            try:
                path = await asyncio.to_thread(backups.create_zip, prefix)
            finally:
                if online and self.state == "online":
                    try:
                        await self.send("save-on")
                    except Exception:  # noqa: BLE001
                        pass
            self.cfg["last_backup"] = int(time.time())
            self.cfg.save()
            await asyncio.to_thread(backups.prune, int(self.cfg["max_backups"]))
            return path

    # ── stats ───────────────────────────────────────────────
    def stats(self):
        if not self._ps:
            return None
        try:
            cpu = self._ps.cpu_percent(None) / (psutil.cpu_count() or 1)
            return cpu, self._ps.memory_info().rss / 1048576
        except Exception:  # noqa: BLE001
            return None
