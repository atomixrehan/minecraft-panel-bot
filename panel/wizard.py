"""Interactive setup.

* First run: asks each question ONCE and saves every answer immediately – if you stop half-way, the next run
  continues with only the unanswered questions.
* Later (`./start.sh --setup`): a menu to change ONE thing (token, channels, RAM, version, plugins, server-list text…).
* `./start.sh --install`: repairs/finishes the server download with no questions.
Starting the bot never asks anything once it is configured.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import installer, props
from .config import SERVER_DIR, Config, parse_ram

B, G, Y, R, X = "\033[1;36m", "\033[1;32m", "\033[1;33m", "\033[1;31m", "\033[0m"
PROPS_FILE = SERVER_DIR / "server.properties"


def say(msg: str = "") -> None:
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


def mark(cfg: Config, step: str) -> None:
    """Remember that a question was answered (and save right away)."""
    if step not in cfg["setup_steps"]:
        cfg["setup_steps"].append(step)
    cfg.save()


def step_done(cfg: Config, step: str) -> bool:
    return "*" in cfg["setup_steps"] or step in cfg["setup_steps"]


# ───────────────────────── the questions ─────────────────────────
def step_token(cfg: Config) -> None:
    while True:
        token = ask("Discord bot token", cfg["token"] or None)
        st, data = api("/users/@me", token)
        if st == 200:
            say(f"  {G}✓ Logged in as {data.get('username')}{X}")
            say(f"  Invite the bot (needs the applications.commands scope):\n  {B}https://discord.com/oauth2/authorize?client_id={data.get('id')}&permissions=536955968&scope=bot%20applications.commands{X}")
            break
        if st == 0:
            say(f"  {Y}! Could not reach Discord to verify the token – continuing.{X}")
            break
        say(f"  {R}! Discord rejected that token (HTTP {st}).{X}")
    cfg["token"] = token
    mark(cfg, "token")


def step_role(cfg: Config) -> None:
    cfg["op_role_id"] = int(ask("OP role ID", cfg["op_role_id"], snowflake))
    mark(cfg, "role")


def _ask_channel(label: str, token: str, default):
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


def step_channels(cfg: Config) -> None:
    token = cfg["token"]
    old_panel = cfg["panel_channel_id"]
    panel, guild = _ask_channel("Panel channel ID", token, cfg["panel_channel_id"])
    cfg["panel_channel_id"] = panel
    cfg["crosschat_channel_id"], _ = _ask_channel("Cross-chat channel ID", token, cfg["crosschat_channel_id"])
    cfg["console_channel_id"], _ = _ask_channel("Console channel ID", token, cfg["console_channel_id"])
    cfg["configure_channel_id"], _ = _ask_channel("Configure channel ID", token, cfg["configure_channel_id"])
    if panel != old_panel:
        cfg["panel_message_id"] = 0   # the old panel lives in another channel
    if guild and cfg["op_role_id"]:
        st, roles = api(f"/guilds/{guild}/roles", token)
        if st == 200 and cfg["op_role_id"] not in {int(r["id"]) for r in roles}:
            say(f"  {Y}! The OP role ID wasn't found in that server – double-check it.{X}")
    mark(cfg, "channels")


def step_eula(cfg: Config) -> None:
    say("\nMinecraft requires you to accept the EULA: https://aka.ms/MinecraftEULA")
    cfg["eula"] = yes("Do you accept the Minecraft EULA?", bool(cfg["eula"]))
    if cfg["eula"]:
        mark(cfg, "eula")
    else:
        say(f"{R}The server cannot run without accepting the EULA – you can accept it later with ./start.sh --setup.{X}")
        cfg.save()


def default_ram() -> int:
    try:
        import psutil
        total = psutil.virtual_memory().total // 1048576
        return max(1024, min(8192, int(total * 0.6) // 512 * 512))
    except Exception:  # noqa: BLE001
        return 2048


def step_ram(cfg: Config) -> None:
    def check(v):
        try:
            parse_ram(v)
        except ValueError as e:
            return str(e)
    cfg["ram_mb"] = parse_ram(ask("RAM for the server (e.g. 4G)", f"{cfg['ram_mb'] if cfg['setup_done'] else default_ram()}M", check))
    mark(cfg, "ram")


def step_version(cfg: Config) -> None:
    say("\nFetching Paper versions…")
    versions: list = []
    try:
        versions = installer.paper_versions()
    except Exception as e:  # noqa: BLE001
        say(f"  {Y}! Could not reach PaperMC ({e}){X}")
    if versions:
        latest = installer.latest_stable_version(versions)
        say(f"  Latest stable: {G}{latest}{X} · recent: {', '.join(versions[:8])}")
        cfg["mc_version"] = ask("Minecraft version", cfg["mc_version"] or latest, lambda v: None if v in versions else "Unknown version")
    else:
        cfg["mc_version"] = ask("Minecraft version (e.g. 1.21.4)", cfg["mc_version"] or None)
    mark(cfg, "version")


def step_plugins(cfg: Config) -> None:
    say(f"\n{B}Plugin presets{X}")
    say("  1) Install recommended  (" + ", ".join(c[1] for c in installer.CATALOG if c[3]) + ")")
    say("  2) Custom               (pick from a list)")
    say("  3) None")
    choice = ask("Choose 1/2/3", "1", lambda v: None if v in ("1", "2", "3") else "Enter 1, 2 or 3")
    plugins: list = []
    if choice == "1":
        plugins = list(installer.RECOMMENDED)
    elif choice == "2":
        for i, (_, name, desc, rec) in enumerate(installer.CATALOG, 1):
            say(f"  {i:>2}) {name}{' *' if rec else ''} – {desc}")
        raw = ask("Numbers separated by commas (e.g. 1,2,3,7)", "")
        for part in raw.replace(" ", "").split(","):
            if part.isdigit() and 1 <= int(part) <= len(installer.CATALOG):
                plugins.append(installer.CATALOG[int(part) - 1][0])
    cfg["plugins"] = list(dict.fromkeys(plugins))
    say(f"{G}DiscordSRV{X} (cross-chat) is always installed and auto-configured with your token and cross-chat channel.")
    mark(cfg, "plugins")


def step_options(cfg: Config) -> None:
    say(f"\n{B}Server details{X} (press Enter to keep the default)")
    current = props.read(PROPS_FILE)
    motd = ask("Server name / MOTD shown in the Minecraft server list (&a = colours, \\n = 2nd line)", current.get("motd") or "A Minecraft Server")
    props.write_key(PROPS_FILE, "motd", props.motd_value(motd), raw=True)
    maxp = ask("Max players", current.get("max-players") or "20", lambda v: None if v.isdigit() and 1 <= int(v) <= 1000 else "Enter a number 1-1000")
    props.write_key(PROPS_FILE, "max-players", maxp)
    a = cfg["autoshut"]
    a["enabled"] = yes("Auto-shutdown when nobody is online?", a["enabled"])
    if a["enabled"]:
        a["minutes"] = int(ask("  …after how many idle minutes?", a["minutes"], lambda v: None if v.isdigit() and 1 <= int(v) <= 240 else "Enter 1-240"))
    cfg["autostart"] = yes("Auto-start the server when someone tries to join while it's offline?", bool(cfg["autostart"]))
    mark(cfg, "options")


STEPS = [
    ("token", step_token), ("role", step_role), ("channels", step_channels), ("eula", step_eula),
    ("ram", step_ram), ("version", step_version), ("plugins", step_plugins), ("options", step_options),
]


# ───────────────────────── install / repair ─────────────────────────
def install(cfg: Config, log=say) -> bool:
    """Download/finish everything the server needs. Asks nothing. Safe to run again at any time."""
    say(f"\n{B}Installing server…{X}")
    ok = True
    try:
        SERVER_DIR.mkdir(parents=True, exist_ok=True)
        installer.ensure_java(installer.required_java(cfg["mc_version"]), log)
        first_time = not (SERVER_DIR / "server.jar").exists()
        installer.ensure_server_files(cfg, log, with_plugins=True)
        if cfg["eula"] and first_time:
            installer.bootstrap(cfg, log)   # one boot so plugins generate their configs, then they get wired together
        elif installer.patch_configs(cfg):
            log("  plugin configs wired together")
    except Exception as e:  # noqa: BLE001
        ok = False
        say(f"{R}! Install step failed: {e}{X}")
    missing = installer.missing_plugins(cfg)
    if missing:
        ok = False
        say(f"{Y}! Not installed yet: {', '.join(missing)}{X}")
    cfg["install_done"] = bool(ok and (SERVER_DIR / "server.jar").exists())
    cfg.save()
    if cfg["install_done"]:
        say(f"{G}✓ Server files are installed.{X}")
        say("Open ports on your firewall/host: TCP 25565 (Java)" + (" and UDP 19132 (Bedrock)" if "geyser" in cfg["plugins"] else "") + ".")
    else:
        say(f"{Y}Some downloads did not finish. That's fine: the bot finishes them itself the next time you press Start,")
        say(f"or run  ./start.sh --install  to retry now. Nothing needs to be re-entered.{X}")
    return cfg["install_done"]


# ───────────────────────── entry points ─────────────────────────
def run(cfg: Config) -> None:
    """First run (or resume of an interrupted one): ask only what hasn't been answered yet."""
    pending = [(sid, fn) for sid, fn in STEPS if not step_done(cfg, sid)]
    say(f"\n{B}══════════ Minecraft Panel Bot – setup ══════════{X}")
    if len(pending) < len(STEPS):
        say(f"{G}Found an unfinished setup – continuing where you left off.{X} Your earlier answers are kept.\n")
    else:
        say("You need: a bot token, the OP role ID and 4 channel IDs (panel, cross-chat, console, configure).")
        say(f"{Y}Reminder:{X} in the Developer Portal → Bot, enable BOTH 'Message Content Intent' and 'Server Members Intent'")
        say("(this bot and the DiscordSRV cross-chat plugin both use them). Every answer is saved as you go.\n")
    for sid, fn in pending:
        fn(cfg)
    cfg["setup_done"] = True
    cfg.save()
    say(f"\n{G}✓ Configuration saved to config.json{X}")
    install(cfg)
    say(f"\n{G}✓ Setup complete.{X}")


def reconfigure(cfg: Config) -> None:
    """Menu: change one setting without going through everything again."""
    def do_version():
        old = installer.installed_version()
        step_version(cfg)
        if cfg["mc_version"] != old:
            say(f"{Y}Tip: make a backup first (/backup create). Downgrading a world can corrupt it.{X}")
            if yes(f"Download Paper {cfg['mc_version']} now?", True):
                try:
                    installer.download_server(cfg["mc_version"], say)
                    say("Updating plugins for the new version…")
                    installer.update_all(cfg["mc_version"], say)
                except Exception as e:  # noqa: BLE001
                    say(f"{R}! {e}{X}")

    def do_plugins():
        step_plugins(cfg)
        installer.install_many(installer.missing_plugins(cfg), cfg["mc_version"], say)

    def do_all():
        for _, fn in STEPS:
            fn(cfg)
        install(cfg)

    items = [
        ("1", "Bot token", lambda: step_token(cfg)),
        ("2", "OP role + the 4 channels", lambda: (step_role(cfg), step_channels(cfg))),
        ("3", "RAM", lambda: step_ram(cfg)),
        ("4", "Minecraft version", do_version),
        ("5", "Plugins (recommended set / custom picks)", do_plugins),
        ("6", "Server name (MOTD), max players, auto-shutdown, auto-start", lambda: step_options(cfg)),
        ("7", "Minecraft EULA", lambda: step_eula(cfg)),
        ("8", "Repair / finish the server download (no questions)", lambda: install(cfg)),
        ("9", "Redo EVERYTHING (the full wizard)", do_all),
    ]
    say(f"\n{B}══════════ Change a setting ══════════{X}")
    while True:
        say("")
        for key, label, _ in items:
            say(f"  {key}) {label}")
        say("  0) Done")
        choice = ask("What do you want to change?", "0", lambda v: None if v in [i[0] for i in items] + ["0"] else "Pick a number from the list")
        if choice == "0":
            break
        next(fn for key, _, fn in items if key == choice)()
        cfg.save()
        say(f"{G}✓ Saved.{X}")
    say(f"\n{G}Done.{X} Start the bot with ./start.sh   (it never asks questions again).\n")


def update(cfg: Config) -> None:
    """Bot-code update check. READ-ONLY: never writes config.json, server/, data/ or backups/."""
    from .config import SERVER_DIR
    say(f"\n{B}══════════ Update check ══════════{X}")
    say(f"  config.json   {G}kept as is{X}  (new options automatically use safe defaults)")
    say(f"  server/       {G}kept as is{X}  (world, plugins, server.properties untouched)")
    say(f"  Minecraft     Paper {cfg['mc_version'] or '?'} · world folder present: {'yes' if SERVER_DIR.exists() else 'no'}")
    say(f"  panel message {G}kept{X}  (it will be edited, not re-posted)")
    say(f"\n{G}✓ Only the bot code, scripts and Python dependencies were updated.{X}\n")


def update_server(cfg: Config) -> None:
    """OPTIONAL: update the Paper build + plugins (changes files inside server/). Config, world, backups are untouched."""
    say(f"\n{B}══════════ Updating ══════════{X}")
    problems = 0
    version = cfg["mc_version"]
    if not version:
        say(f"  {Y}! No Minecraft version in config.json – skipping the Paper update.{X}")
        problems += 1
    else:
        try:
            say(f"Checking Paper {version} for a newer build…")
            say("  " + (f"{G}✓ updated{X}" if installer.update_server_build(version, say) else "already on the latest build"))
        except Exception as e:  # noqa: BLE001
            problems += 1
            say(f"  {Y}! Paper update skipped: {e}{X}")
    newly_installed = False
    for core in installer.CORE_PLUGINS:  # e.g. DiscordSRV, added in newer versions
        have = any(core in f.name.lower() for f in installer.PLUGIN_DIR.glob("*.jar")) if installer.PLUGIN_DIR.exists() else False
        if not have:
            say(f"Installing missing core plugin: {core}")
            try:
                installer.install_many([core], version, say)
            except Exception as e:  # noqa: BLE001
                say(f"  {Y}! {core}: {e}{X}")
            if any(core in f.name.lower() for f in installer.PLUGIN_DIR.glob("*.jar")):
                newly_installed = True
            else:
                problems += 1
                say(f"  {Y}! {core} could not be installed – cross-chat won't work until it is. Re-run the update when online.{X}")
    say("Updating plugins…")
    try:
        updated = installer.update_all(version, say)
        say("  " + (", ".join(updated) if updated else "all plugins up to date"))
    except Exception as e:  # noqa: BLE001
        problems += 1
        say(f"  {Y}! Plugin update failed: {e}{X}")
    try:
        installer.ensure_server_icon(say)
        if newly_installed and cfg["eula"]:
            installer.bootstrap(cfg, say)   # first boot generates the new plugin's config, then it is wired up
        elif installer.patch_configs(cfg):
            say("  plugin configs re-wired")
    except Exception as e:  # noqa: BLE001
        problems += 1
        say(f"  {Y}! Config wiring failed: {e}{X}")
    if problems:
        say(f"\n{Y}Update finished with {problems} warning(s) – see above. Config, world and backups were kept.{X}\n")
    else:
        say(f"\n{G}✓ Update complete.{X} Config, world and backups were kept; the existing panel message will be edited, not re-posted.\n")


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
