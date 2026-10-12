"""Interactive first-run setup (token, channels, version, plugin presets)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import installer
from .config import ID_KEYS, SERVER_DIR, Config, parse_ram

B, G, Y, R, X = "\033[1;36m", "\033[1;32m", "\033[1;33m", "\033[1;31m", "\033[0m"


def say(msg: str) -> None:
    print(msg, flush=True)


def ask(prompt: str, default=None, check=None) -> str:
    while True:
        suffix = f" [{default}]" if default not in (None, "", 0) else ""
        raw = input(f"{B}?{X} {prompt}{suffix}: ").strip()
        if not raw and default not in (None, "", 0):
            raw = str(default)
        if not raw:
            say(f"  {R}! required{X}")
            continue
        if check:
            err = check(raw)
            if err:
                say(f"  {R}! {err}{X}")
                continue
        return raw


def yes(prompt: str, default: bool = False) -> bool:
    raw = input(f"{B}?{X} {prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not raw else raw.startswith("y")


def snowflake(raw: str):
    return None if raw.isdigit() and 15 <= len(raw) <= 21 else "Enter a numeric Discord ID (enable Developer Mode → right-click → Copy ID)"


def api(path: str, token: str):
    req = urllib.request.Request("https://discord.com/api/v10" + path,
                                 headers={"Authorization": f"Bot {token}", "User-Agent": "DiscordBot (minecraft-panel, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001
        return 0, None


def ask_token(cfg: Config) -> str:
    while True:
        token = ask("Discord bot token", cfg["token"] or None)
        st, data = api("/users/@me", token)
        if st == 200:
            say(f"  {G}✓ Logged in as {data.get('username')}{X}")
            return token
        if st == 0:
            say(f"  {Y}! Could not reach Discord to verify the token – continuing.{X}")
            return token
        say(f"  {R}! Discord rejected that token (HTTP {st}).{X}")


def ask_channel(label: str, token: str, default):
    while True:
        cid = int(ask(label, default or None, snowflake))
        st, data = api(f"/channels/{cid}", token)
        if st == 200:
            say(f"  {G}✓ #{data.get('name')}{X}")
            return cid, data.get("guild_id")
        if st == 0:
            return cid, None
        say(f"  {Y}! Can't see that channel (HTTP {st}). Invite the bot to your server and give it View Channel first.{X}")
        if yes("  Use it anyway?"):
            return cid, None


def default_ram() -> int:
    try:
        import psutil
        total = psutil.virtual_memory().total // 1048576
        return max(1024, min(8192, int(total * 0.6) // 512 * 512))
    except Exception:  # noqa: BLE001
        return 2048


def run(cfg: Config) -> None:
    say(f"\n{B}══════════ Minecraft Panel Bot – setup ══════════{X}")
    say("You need: a bot token, the OP role ID and 4 channel IDs (panel, cross-chat, console, configure).")
    say(f"{Y}Reminder:{X} in the Developer Portal → Bot, enable BOTH 'Message Content Intent' and 'Server Members Intent'")
    say("(this bot and the DiscordSRV cross-chat plugin both use them).\n")

    token = ask_token(cfg)
    role = int(ask("OP role ID", cfg["op_role_id"], snowflake))
    panel, guild = ask_channel("Panel channel ID", token, cfg["panel_channel_id"])
    cross, _ = ask_channel("Cross-chat channel ID", token, cfg["crosschat_channel_id"])
    console, _ = ask_channel("Console channel ID", token, cfg["console_channel_id"])
    conf, _ = ask_channel("Configure channel ID", token, cfg["configure_channel_id"])

    if guild:
        st, roles = api(f"/guilds/{guild}/roles", token)
        if st == 200 and role not in {int(r["id"]) for r in roles}:
            say(f"  {Y}! That role ID wasn't found in the server – double-check it.{X}")

    cfg["token"], cfg["op_role_id"] = token, role
    cfg["panel_channel_id"], cfg["crosschat_channel_id"] = panel, cross
    cfg["console_channel_id"], cfg["configure_channel_id"] = console, conf
    cfg["panel_message_id"] = 0

    say("\nMinecraft requires you to accept the EULA: https://aka.ms/MinecraftEULA")
    cfg["eula"] = yes("Do you accept the Minecraft EULA?", bool(cfg["eula"]))
    if not cfg["eula"]:
        say(f"{R}The server cannot run without accepting the EULA. Re-run with ./start.sh --setup.{X}")

    def ram_check(v):
        try:
            parse_ram(v)
        except ValueError as e:
            return str(e)

    cfg["ram_mb"] = parse_ram(ask("RAM for the server (e.g. 4G)", f"{default_ram()}M", ram_check))

    say("\nFetching Paper versions…")
    versions: list = []
    try:
        versions = installer.paper_versions()
    except Exception as e:  # noqa: BLE001
        say(f"  {Y}! Could not reach PaperMC ({e}){X}")
    if versions:
        latest = installer.latest_stable_version(versions)
        say(f"  Latest stable: {G}{latest}{X} · recent: {', '.join(versions[:8])}")
        cfg["mc_version"] = ask("Minecraft version", cfg["mc_version"] or latest,
                                lambda v: None if v in versions else "Unknown version")
    else:
        cfg["mc_version"] = ask("Minecraft version (e.g. 1.21.4)", cfg["mc_version"] or None)

    say(f"\n{B}Plugin presets{X}")
    say("  1) Install recommended  (" + ", ".join(c[1] for c in installer.CATALOG if c[3]) + ")")
    say("  2) Custom               (pick from a list)")
    say("  3) None")
    choice = ask("Choose 1/2/3", "1", lambda v: None if v in ("1", "2", "3") else "Enter 1, 2 or 3")
    plugins: list = []
    if choice == "1":
        plugins = installer.RECOMMENDED
    elif choice == "2":
        for i, (_, name, desc, rec) in enumerate(installer.CATALOG, 1):
            say(f"  {i:>2}) {name}{' *' if rec else ''} – {desc}")
        raw = ask("Numbers separated by commas (e.g. 1,2,3,7)", "")
        for part in raw.replace(" ", "").split(","):
            if part.isdigit() and 1 <= int(part) <= len(installer.CATALOG):
                plugins.append(installer.CATALOG[int(part) - 1][0])

    say(f"\n{G}DiscordSRV{X} (cross-chat) is always installed and auto-configured with your token and cross-chat channel.")
    plugins = list(dict.fromkeys(installer.CORE_PLUGINS + plugins))

    cfg["setup_done"] = True
    cfg.save()
    say(f"\n{G}✓ Configuration saved to config.json{X}\n")

    # ── install everything ──
    say(f"{B}Installing server…{X}")
    try:
        SERVER_DIR.mkdir(parents=True, exist_ok=True)
        installer.ensure_java(installer.required_java(cfg["mc_version"]), say)
        if installer.installed_version() != cfg["mc_version"] or not (SERVER_DIR / "server.jar").exists():
            installer.download_server(cfg["mc_version"], say)
        if plugins:
            installer.install_many(plugins, cfg["mc_version"], say)
        installer.ensure_server_icon(say)
        if cfg["eula"]:
            installer.bootstrap(cfg, say)
    except Exception as e:  # noqa: BLE001
        say(f"{R}! Install step failed: {e}{X}")
        say("  The bot will still start; fix the problem and run ./start.sh --setup again.")
    say(f"\n{G}✓ Setup complete.{X}")
    say("Open ports on your firewall/host: TCP 25565 (Java)" + (" and UDP 19132 (Bedrock)" if "geyser" in plugins else "") + ".\n")


def update(cfg: Config) -> None:
    """Keep config, world and plugins; update Paper build + plugins + wiring."""
    say(f"\n{B}══════════ Updating ══════════{X}")
    version = cfg["mc_version"]
    try:
        say(f"Checking Paper {version} for a newer build…")
        say("  " + (f"{G}✓ updated{X}" if installer.update_server_build(version, say) else "already on the latest build"))
    except Exception as e:  # noqa: BLE001
        say(f"  {Y}! Paper update skipped: {e}{X}")
    present = {m.get("project_id") for m in installer.load_index().values()}
    for core in installer.CORE_PLUGINS:  # e.g. DiscordSRV added in newer versions
        if core not in present and not any(core in f.name.lower() for f in installer.PLUGIN_DIR.glob("*.jar")):
            say(f"Installing missing core plugin: {core}")
            installer.install_many([core], version, say)
    say("Updating plugins…")
    updated = installer.update_all(version, say)
    say("  " + (", ".join(updated) if updated else "all plugins up to date"))
    installer.ensure_server_icon(say)
    if installer.patch_configs(cfg):
        say("  plugin configs re-wired")
    say(f"\n{G}✓ Update complete.{X} Your config, world and backups were kept.\n")


def cleanup(cfg: Config) -> None:
    """Best effort: delete the old panel message before a clean reinstall."""
    if not (cfg["token"] and cfg["panel_channel_id"] and cfg["panel_message_id"]):
        return
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{cfg['panel_channel_id']}/messages/{cfg['panel_message_id']}", method="DELETE",
        headers={"Authorization": f"Bot {cfg['token']}", "User-Agent": "DiscordBot (minecraft-panel, 1.0)"})
    try:
        urllib.request.urlopen(req, timeout=15)
        say("Old panel message deleted from Discord.")
    except Exception:  # noqa: BLE001
        say("(Could not delete the old panel message – remove it by hand if it is still there.)")
