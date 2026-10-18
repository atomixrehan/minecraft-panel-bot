"""Tiny Minecraft protocol helpers: Java status ping (TCP) and Bedrock/RakNet ping (UDP)."""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import time
from typing import Optional

RAKNET_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


# ── Java protocol ──────────────────────────────────────────────
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


async def _read_packet(reader: asyncio.StreamReader, max_len: int = 4096):
    length = await _read_varint(reader)
    if length <= 0 or length > max_len:
        raise ValueError("bad packet length")
    data = await reader.readexactly(length)
    pid, off = _parse_varint(data, 0)
    return pid, data[off:]


async def status_ping(host: str, port: int, timeout: float = 3.0) -> Optional[dict]:
    """Server-list ping. Returns the status JSON, or None if the server can't be reached."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except Exception:  # noqa: BLE001
        return None
    try:
        writer.write(_packet(0, _varint(767) + _string(host) + port.to_bytes(2, "big") + _varint(1)) + _packet(0))
        await writer.drain()
        pid, payload = await asyncio.wait_for(_read_packet(reader, 4_000_000), timeout)
        if pid != 0:
            return None
        n, off = _parse_varint(payload, 0)
        return json.loads(payload[off:off + n])
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


# ── Bedrock (RakNet) ───────────────────────────────────────────
def build_pong(ping_time: bytes, guid: int, server_id: str) -> bytes:
    """RakNet 'unconnected pong' (0x1c) carrying the Bedrock server-list string."""
    s = server_id.encode("utf-8")
    return b"\x1c" + ping_time + struct.pack(">q", guid) + RAKNET_MAGIC + struct.pack(">H", len(s)) + s


def raknet_ping(host: str, port: int, timeout: float = 2.0) -> Optional[str]:
    """Bedrock server-list ping (blocking). Returns the 'MCPE;…' string or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(b"\x01" + struct.pack(">Q", int(time.time() * 1000) & 0xFFFFFFFFFFFFFFFF) + RAKNET_MAGIC + struct.pack(">q", 2), (host, port))
        data, _ = sock.recvfrom(4096)
        if data[:1] == b"\x1c" and len(data) >= 35:
            (n,) = struct.unpack(">H", data[33:35])
            return data[35:35 + n].decode("utf-8", "replace")
    except OSError:
        pass
    finally:
        sock.close()
    return None
