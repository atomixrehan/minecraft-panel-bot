"""Everything that downloads/installs things: Java, Paper, plugins (Modrinth)."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from .config import DATA_DIR, RUNTIME_DIR, SERVER_DIR

PLUGIN_DIR = SERVER_DIR / "plugins"
UA = {"User-Agent": "MinecraftPanelBot/1.0 (open-source discord panel)"}

# (modrinth slug, name, description, recommended?)
CATALOG = [
    ("geyser", "Geyser", "Lets Bedrock (mobile/console/Win10) players join", True),
    ("floodgate", "Floodgate", "Bedrock players join without a Java account ('.' name prefix)", True),
    ("viaversion", "ViaVersion", "Newer Java clients can join", True),
    ("viabackwards", "ViaBackwards", "Older Java clients can join", True),
    ("viarewind", "ViaRewind", "Very old (1.7/1.8) Java clients can join", True),
    ("skinsrestorer", "SkinsRestorer", "Proper skins for Bedrock/offline players", True),
    ("spark", "spark", "Performance profiler, TPS & lag diagnostics", True),
    ("luckperms", "LuckPerms", "Permissions / ranks", False),
    ("essentialsx", "EssentialsX", "Homes, warps, kits, economy, /tpa …", False),
    ("chunky", "Chunky", "World pre-generation", False),
    ("worldedit", "WorldEdit", "In-game building tool", False),
    ("coreprotect", "CoreProtect", "Block logging & rollback (anti-grief)", False),
]
RECOMMENDED = [c[0] for c in CATALOG if c[3]]
CORE_PLUGINS = ["discordsrv"]   # always installed: powers the cross-chat channel

GITHUB_FALLBACK = {"discordsrv": "DiscordSRV/DiscordSRV"}
LOADERS = ["paper", "spigot", "bukkit", "purpur"]
MR = "https://api.modrinth.com/v2"
DIRECT = {  # fallback if Modrinth is unreachable / has no matching file
    "geyser": ("https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/spigot", "Geyser-Spigot.jar"),
    "floodgate": ("https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest/downloads/spigot", "floodgate-spigot.jar"),
}


def get_json(url: str, **params):
    r = requests.get(url, params=params or None, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def download(url: str, dest: Path, expected: Optional[tuple] = None, max_bytes: Optional[int] = None) -> None:
    """Download atomically; expected=('sha256'|'sha512'|'sha1', hexdigest)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.new(expected[0]) if expected and expected[1] else None
    with requests.get(url, headers=UA, stream=True, timeout=(15, 120), allow_redirects=True) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            total = 0
            for chunk in r.iter_content(262144):
                total += len(chunk)
                if max_bytes and total > max_bytes:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("Download is larger than the allowed maximum")
                f.write(chunk)
                if h:
                    h.update(chunk)
    if h and h.hexdigest().lower() != expected[1].lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Checksum mismatch for {dest.name}")
    os.replace(tmp, dest)


# ───────────────────────── versions / java ─────────────────────────
def vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0]))


def required_java(mc: str) -> int:
    k = vkey(mc)
    if not k:
        return 21
    if k[0] >= 26:          # year-based versions (26.x) -> Java 25
        return 25
    minor = k[1] if len(k) > 1 else 0
    patch = k[2] if len(k) > 2 else 0
    if minor >= 21 or (minor == 20 and patch >= 5):
        return 21
    if minor >= 17:
        return 17
    return 11


def java_major(java: str) -> Optional[int]:
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=20)
        m = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
        if not m:
            return None
        major = int(m.group(1))
        return int(m.group(2)) if major == 1 and m.group(2) else major
    except Exception:
        return None


def _runtime_java(feature: int) -> Optional[str]:
    for p in (RUNTIME_DIR / f"java{feature}").glob("**/bin/java"):
        return str(p)
    return None


def ensure_java(feature: int, log: Callable = print) -> str:
    p = _runtime_java(feature)
    if p:
        return p
    sysjava = shutil.which("java")
    if sysjava:
        m = java_major(sysjava)
        if m and m >= feature:
            return sysjava
    if platform.system() not in ("Linux", "Darwin"):
        raise RuntimeError("Automatic Java download supports Linux/macOS. Install Java manually.")
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "aarch64", "arm64": "aarch64"}.get(platform.machine().lower())
    if not arch:
        raise RuntimeError(f"Unsupported CPU architecture: {platform.machine()}")
    os_name = "mac" if platform.system() == "Darwin" else ("alpine-linux" if Path("/etc/alpine-release").exists() else "linux")
    log(f"Downloading Java {feature} (Eclipse Temurin)…")
    base = RUNTIME_DIR / f"java{feature}"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    archive = RUNTIME_DIR / f"java{feature}.tar.gz"
    last: Optional[Exception] = None
    for image in ("jre", "jdk"):
        try:
            download(f"https://api.adoptium.net/v3/binary/latest/{feature}/ga/{os_name}/{arch}/{image}/hotspot/normal/eclipse", archive)
            last = None
            break
        except Exception as e:  # noqa: BLE001
            last = e
    if last is not None:
        raise RuntimeError(f"Could not download Java {feature}: {last}")
    with tarfile.open(archive) as tar:
        try:
            tar.extractall(base, filter="data")
        except TypeError:
            tar.extractall(base)
    archive.unlink(missing_ok=True)
    p = _runtime_java(feature)
    if not p:
        raise RuntimeError("Java archive extracted but no bin/java found")
    os.chmod(p, 0o755)
    return p


# ───────────────────────────── Paper ─────────────────────────────
PAPER_V3 = "https://fill.papermc.io/v3/projects/paper"
PAPER_V2 = "https://api.papermc.io/v2/projects/paper"


def paper_versions() -> list:
    try:
        vs = get_json(PAPER_V3)["versions"]
        flat = [v for lst in vs.values() for v in lst] if isinstance(vs, dict) else list(vs)
    except Exception:  # noqa: BLE001
        flat = get_json(PAPER_V2)["versions"]
    flat = [v for v in flat if re.fullmatch(r"\d+(\.\d+)+", v)]
    return sorted(set(flat), key=vkey, reverse=True)


def paper_build(version: str, stable_only: bool = False):
    """-> (url, filename, sha256|None)"""
    try:
        data = get_json(f"{PAPER_V3}/versions/{version}/builds")
    except Exception:  # noqa: BLE001
        data = None
    if data is not None:
        builds = data if isinstance(data, list) else data.get("builds", [])
        pool = [b for b in builds if b.get("channel") == "STABLE"] if stable_only else builds
        if pool:
            b = max(pool, key=lambda x: x.get("id", 0))
            dl = b["downloads"]["server:default"]
            return dl["url"], dl["name"], (dl.get("checksums") or {}).get("sha256")
        if stable_only:
            raise LookupError(f"No stable Paper build for {version}")
    builds = get_json(f"{PAPER_V2}/versions/{version}")["builds"]
    if not builds:
        raise LookupError(f"No Paper build for {version}")
    n = max(builds)
    info = get_json(f"{PAPER_V2}/versions/{version}/builds/{n}")
    dl = info["downloads"]["application"]
    return f"{PAPER_V2}/versions/{version}/builds/{n}/downloads/{dl['name']}", dl["name"], dl.get("sha256")


def latest_stable_version(versions: list) -> str:
    for v in versions[:8]:
        try:
            paper_build(v, stable_only=True)
            return v
        except Exception:  # noqa: BLE001
            continue
    return versions[0]


def installed_version() -> Optional[str]:
    try:
        return json.loads((DATA_DIR / "jar.json").read_text())["version"]
    except Exception:  # noqa: BLE001
        return None


def download_server(version: str, log: Callable = print) -> None:
    url, name, sha = paper_build(version)
    log(f"Downloading Paper {version} ({name})…")
    download(url, SERVER_DIR / "server.jar", ("sha256", sha) if sha else None)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "jar.json").write_text(json.dumps({"software": "paper", "version": version, "build": name}))


# ──────────────────────────── plugins ────────────────────────────
def _index_path() -> Path:
    return DATA_DIR / "plugins.json"


def load_index() -> dict:
    try:
        idx = json.loads(_index_path().read_text())
    except Exception:  # noqa: BLE001
        idx = {}
    return {fn: m for fn, m in idx.items() if (PLUGIN_DIR / fn).exists()}


def save_index(idx: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _index_path().write_text(json.dumps(idx, indent=1))


def list_plugins() -> list:
    idx = load_index()
    out = []
    for p in sorted(PLUGIN_DIR.glob("*.jar"), key=lambda x: x.name.lower()):
        m = idx.get(p.name, {})
        out.append({"file": p.name, "title": m.get("title") or p.stem, "version": m.get("version", "?")})
    return out


def remove_plugin(filename: str) -> None:
    if Path(filename).name != filename or not filename.endswith(".jar"):
        raise ValueError("Invalid plugin file name")
    (PLUGIN_DIR / filename).unlink(missing_ok=True)
    idx = load_index()
    idx.pop(filename, None)
    save_index(idx)


def mr_search(query: str, mc_version: Optional[str] = None, limit: int = 10) -> list:
    def go(facets):
        return get_json(f"{MR}/search", query=query, facets=json.dumps(facets), limit=limit, index="relevance")["hits"]
    facets = [["project_type:plugin"]]
    if mc_version:
        hits = go(facets + [[f"versions:{mc_version}"]])
        if hits:
            return hits
    return go(facets)


def _pick_version(project: str, mc_version: Optional[str]):
    params = {"loaders": json.dumps(LOADERS)}
    if mc_version:
        params["game_versions"] = json.dumps([mc_version])
    vs = get_json(f"{MR}/project/{project}/version", **params)
    return vs[0] if vs else None


def _install_modrinth(project: str, mc_version: Optional[str], log, seen: set) -> list:
    if project in seen:
        return []
    seen.add(project)
    proj = get_json(f"{MR}/project/{project}")
    seen.add(proj["id"])
    ver, compatible = _pick_version(project, mc_version), True
    if not ver:
        ver, compatible = _pick_version(project, None), False
    if not ver:
        raise LookupError(f"No Paper/Spigot-compatible release found for '{project}'")
    f = next((x for x in ver["files"] if x.get("primary")), ver["files"][0])
    idx = load_index()
    for fn, m in list(idx.items()):  # drop older jar of same project
        if m.get("project_id") == proj["id"] and fn != f["filename"]:
            (PLUGIN_DIR / fn).unlink(missing_ok=True)
            idx.pop(fn)
    log(f"Installing {proj['title']} {ver['version_number']}…")
    sha = f.get("hashes", {}).get("sha512")
    download(f["url"], PLUGIN_DIR / f["filename"], ("sha512", sha) if sha else None)
    idx[f["filename"]] = {"project_id": proj["id"], "title": proj["title"],
                          "version_id": ver["id"], "version": ver["version_number"]}
    save_index(idx)
    done = [f"{proj['title']} {ver['version_number']}" + ("" if compatible or not mc_version else f" (⚠ not marked for {mc_version})")]
    for d in ver.get("dependencies", []):
        if d.get("dependency_type") == "required" and d.get("project_id") and d["project_id"] not in seen:
            done += _install_modrinth(d["project_id"], mc_version, log, seen)
    return done


def install_plugin(project: str, mc_version: Optional[str] = None, log: Callable = print, _seen: Optional[set] = None) -> list:
    """Install by Modrinth slug/id (+ required deps). Returns list of installed names."""
    try:
        return _install_modrinth(project, mc_version, log, _seen if _seen is not None else set())
    except Exception as e:  # noqa: BLE001
        if project in DIRECT:
            log(f"Modrinth failed for {project} ({e}); using GeyserMC download…")
            url, fn = DIRECT[project]
            download(url, PLUGIN_DIR / fn)
            idx = load_index()
            idx[fn] = {"project_id": project, "title": project.title(), "version_id": "direct", "version": "latest"}
            save_index(idx)
            return [f"{project.title()} (latest)"]
        if project in GITHUB_FALLBACK:
            log(f"Modrinth failed for {project} ({e}); using GitHub release…")
            rel = get_json(f"https://api.github.com/repos/{GITHUB_FALLBACK[project]}/releases/latest")
            asset = next((a for a in rel.get("assets", []) if a["name"].endswith(".jar") and "sources" not in a["name"]), None)
            if not asset:
                raise LookupError(f"No .jar in latest {project} release")
            download(asset["browser_download_url"], PLUGIN_DIR / asset["name"])
            idx = load_index()
            for fn, m in list(idx.items()):
                if m.get("project_id") == project and fn != asset["name"]:
                    (PLUGIN_DIR / fn).unlink(missing_ok=True)
                    idx.pop(fn)
            idx[asset["name"]] = {"project_id": project, "title": project, "version_id": "direct", "version": rel.get("tag_name", "latest")}
            save_index(idx)
            return [f"{project} {rel.get('tag_name', '')}"]
        raise


def update_all(mc_version: Optional[str], log: Callable = print) -> list:
    updated = []
    for fn, m in list(load_index().items()):
        pid = m.get("project_id")
        if not pid or (m.get("version_id") == "direct" and pid not in DIRECT and pid not in GITHUB_FALLBACK):
            continue
        try:
            if m.get("version_id") == "direct":
                install_plugin(pid, mc_version, log)
                updated.append(m["title"])
                continue
            ver = _pick_version(pid, mc_version)
            if ver and ver["id"] != m.get("version_id"):
                install_plugin(pid, mc_version, log)
                updated.append(m["title"])
        except Exception as e:  # noqa: BLE001
            log(f"Could not update {m.get('title', fn)}: {e}")
    return updated


def install_many(keys: list, mc_version: str, log: Callable = print) -> None:
    seen: set = set()
    for key in keys:
        try:
            install_plugin(key, mc_version, log, seen)
        except Exception as e:  # noqa: BLE001
            log(f"  ! {key}: {e}")


# ─────────────────── first boot & config patching ───────────────────
def _patch(path: Path, pattern: str, repl) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    new = re.sub(pattern, repl, text, flags=re.M)
    if new != text:
        path.write_text(new, encoding="utf-8")
        return True
    return False


def patch_configs(cfg=None) -> bool:
    """Wire plugins together. Returns True if any file changed (=> restart needed)."""
    changed = False
    if (PLUGIN_DIR / "floodgate").exists():  # Geyser authenticates through Floodgate, clean Bedrock names
        changed |= _patch(PLUGIN_DIR / "Geyser-Spigot" / "config.yml", r"^(\s*)auth-type:\s*online\b", r"\1auth-type: floodgate")
        fg = PLUGIN_DIR / "floodgate" / "config.yml"
        changed |= _patch(fg, r"^(\s*)replace-spaces:\s*\S+", r"\1replace-spaces: true")
        changed |= _patch(fg, r"^(\s*)username-prefix:.*$", r'\1username-prefix: "."')
    ds = PLUGIN_DIR / "DiscordSRV" / "config.yml"
    if cfg is not None and ds.exists():  # DiscordSRV: same bot token + cross-chat channel, webhook chat with player heads
        changed |= _patch(ds, r"^BotToken:.*$", lambda m: f'BotToken: "{cfg["token"]}"')
        changed |= _patch(ds, r"^Channels:.*$", lambda m: 'Channels: {"global": "%s"}' % cfg["crosschat_channel_id"])
        changed |= _patch(ds, r"^Experiment_WebhookChatMessageDelivery:.*$", "Experiment_WebhookChatMessageDelivery: true")
    return changed


def ensure_server_icon(log: Callable = print) -> None:
    """Default server-list icon (64x64) if the server has none."""
    from .config import DEFAULT_ICON
    target = SERVER_DIR / "server-icon.png"
    if target.exists() or not DEFAULT_ICON.exists():
        return
    try:
        from PIL import Image
        SERVER_DIR.mkdir(parents=True, exist_ok=True)
        Image.open(DEFAULT_ICON).convert("RGBA").resize((64, 64), Image.LANCZOS).save(target, "PNG")
    except Exception as e:  # noqa: BLE001
        log(f"  ! Could not create default server icon: {e}")


def update_server_build(version: str, log: Callable = print) -> bool:
    """Update Paper to the newest build of the SAME Minecraft version. True if updated."""
    url, name, sha = paper_build(version)
    try:
        current = json.loads((DATA_DIR / "jar.json").read_text()).get("build")
    except Exception:  # noqa: BLE001
        current = None
    if current == name and (SERVER_DIR / "server.jar").exists():
        return False
    download_server(version, log)
    return True


def plugin_present(key: str) -> bool:
    return PLUGIN_DIR.exists() and any(key.lower() in p.name.lower() for p in PLUGIN_DIR.glob("*.jar"))


def wanted_plugins(cfg) -> list:
    return list(dict.fromkeys(CORE_PLUGINS + list(cfg["plugins"])))


def missing_plugins(cfg) -> list:
    return [k for k in wanted_plugins(cfg) if not plugin_present(k)]


def ensure_server_files(cfg, log: Callable = print, with_plugins: bool = False) -> bool:
    """Repair a half-finished install WITHOUT asking anything: server.jar, eula.txt, icon (+ chosen plugins).
    Returns True if server.jar exists afterwards."""
    SERVER_DIR.mkdir(parents=True, exist_ok=True)
    if not (SERVER_DIR / "server.jar").exists():
        version = cfg["mc_version"]
        if not version:
            version = latest_stable_version(paper_versions())
            cfg["mc_version"] = version
            cfg.save()
        download_server(version, log)
    eula = SERVER_DIR / "eula.txt"
    if cfg["eula"] and (not eula.exists() or "eula=true" not in eula.read_text()):
        eula.write_text("eula=true\n")
    ensure_server_icon(log)
    if with_plugins:
        todo = missing_plugins(cfg)
        if todo:
            install_many(todo, cfg["mc_version"], log)
    return (SERVER_DIR / "server.jar").exists()


def bootstrap(cfg, log: Callable = print) -> None:
    """Accept EULA, boot once so plugins generate their configs, then patch them."""
    SERVER_DIR.mkdir(parents=True, exist_ok=True)
    (SERVER_DIR / "eula.txt").write_text("eula=true\n" if cfg["eula"] else "eula=false\n")
    if not cfg["eula"]:
        log("EULA not accepted – skipping first boot.")
        return
    java = ensure_java(required_java(cfg["mc_version"]), log)
    log("First boot to generate configs (about a minute)…")
    proc = subprocess.Popen([java, "-Xms512M", "-Xmx1G", "-jar", "server.jar", "nogui"], cwd=SERVER_DIR,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1)
    q: queue.Queue = queue.Queue()

    def reader():
        for line in proc.stdout:  # type: ignore[union-attr]
            q.put(line)
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    deadline, done = time.time() + 300, False
    while time.time() < deadline:
        try:
            line = q.get(timeout=1)
        except queue.Empty:
            continue
        if line is None:
            break
        if "Done (" in line and "For help" in line:
            done = True
            break
    try:
        if proc.poll() is None:
            proc.stdin.write("stop\n")  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
            proc.wait(timeout=90)
    except Exception:  # noqa: BLE001
        proc.kill()
    if not done:
        log("  ! First boot did not finish cleanly – check server/logs/latest.log")
    patch_configs(cfg)
    ensure_server_icon(log)
