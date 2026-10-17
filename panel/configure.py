"""Slash commands for the configure channel (OP only)."""
from __future__ import annotations

import asyncio
import io
import re
import time
from datetime import datetime
from typing import Awaitable, Callable, Literal, Optional

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from . import backups, installer, props
from .config import BACKUP_DIR, DEFAULT_ICON, SERVER_DIR, parse_ram

PROPS_FILE = SERVER_DIR / "server.properties"
NAME_RE = re.compile(r"^\.?[A-Za-z0-9_]{1,32}$")

# key: (label, kind, extra)   kind = bool | choice | int | str
PROPS = {
    "difficulty": ("Difficulty", "choice", ["peaceful", "easy", "normal", "hard"]),
    "gamemode": ("Default gamemode", "choice", ["survival", "creative", "adventure", "spectator"]),
    "hardcore": ("Hardcore", "bool", None),
    "pvp": ("PvP", "bool", None),
    "allow-flight": ("Allow flight", "bool", None),
    "allow-nether": ("Allow Nether", "bool", None),
    "spawn-monsters": ("Spawn monsters", "bool", None),
    "spawn-animals": ("Spawn animals", "bool", None),
    "spawn-npcs": ("Spawn villagers", "bool", None),
    "generate-structures": ("Generate structures", "bool", None),
    "enable-command-block": ("Command blocks", "bool", None),
    "white-list": ("Whitelist", "bool", None),
    "force-gamemode": ("Force gamemode", "bool", None),
    "online-mode": ("Online mode (premium auth)", "bool", None),
    "max-players": ("Max players", "int", (1, 1000)),
    "view-distance": ("View distance", "int", (2, 32)),
    "simulation-distance": ("Simulation distance", "int", (3, 32)),
    "spawn-protection": ("Spawn protection radius", "int", (0, 1000)),
    "player-idle-timeout": ("AFK kick (minutes, 0 = off)", "int", (0, 100000)),
    "max-world-size": ("Max world size", "int", (1, 29999984)),
    "level-seed": ("World seed (new worlds only)", "str", (0, 64)),
}
PROP_DEFAULTS = {
    "difficulty": "easy", "gamemode": "survival", "hardcore": "false", "pvp": "true", "allow-flight": "false",
    "allow-nether": "true", "spawn-monsters": "true", "spawn-animals": "true", "spawn-npcs": "true",
    "generate-structures": "true", "enable-command-block": "false", "white-list": "false",
    "force-gamemode": "false", "online-mode": "true", "max-players": "20", "view-distance": "10",
    "simulation-distance": "10", "spawn-protection": "16", "player-idle-timeout": "0",
    "max-world-size": "29999984", "level-seed": "",
}
GAMERULES = {
    "keepInventory": ("Keep inventory", "bool", None),
    "mobGriefing": ("Mob griefing", "bool", None),
    "doDaylightCycle": ("Daylight cycle", "bool", None),
    "doWeatherCycle": ("Weather cycle", "bool", None),
    "doFireTick": ("Fire spread", "bool", None),
    "doMobSpawning": ("Mob spawning", "bool", None),
    "doInsomnia": ("Phantoms (insomnia)", "bool", None),
    "fallDamage": ("Fall damage", "bool", None),
    "fireDamage": ("Fire damage", "bool", None),
    "drowningDamage": ("Drowning damage", "bool", None),
    "naturalRegeneration": ("Natural regeneration", "bool", None),
    "showDeathMessages": ("Death messages", "bool", None),
    "doImmediateRespawn": ("Instant respawn", "bool", None),
    "announceAdvancements": ("Announce advancements", "bool", None),
    "doEntityDrops": ("Entity drops", "bool", None),
    "doTileDrops": ("Block drops", "bool", None),
    "doPatrolSpawning": ("Pillager patrols", "bool", None),
    "doTraderSpawning": ("Wandering traders", "bool", None),
    "doWardenSpawning": ("Wardens", "bool", None),
    "universalAnger": ("Universal anger", "bool", None),
    "commandBlockOutput": ("Command block output", "bool", None),
    "playersSleepingPercentage": ("Players sleeping % to skip night", "int", (0, 100)),
    "randomTickSpeed": ("Random tick speed", "int", (0, 1000)),
    "spawnRadius": ("Spawn radius", "int", (0, 128)),
    "maxEntityCramming": ("Max entity cramming", "int", (0, 1000)),
}
GR_DEFAULTS = {
    "keepInventory": False, "mobGriefing": True, "doDaylightCycle": True, "doWeatherCycle": True,
    "doFireTick": True, "doMobSpawning": True, "doInsomnia": True, "fallDamage": True, "fireDamage": True,
    "drowningDamage": True, "naturalRegeneration": True, "showDeathMessages": True, "doImmediateRespawn": False,
    "announceAdvancements": True, "doEntityDrops": True, "doTileDrops": True, "doPatrolSpawning": True,
    "doTraderSpawning": True, "doWardenSpawning": True, "universalAnger": False, "commandBlockOutput": True,
    "playersSleepingPercentage": 100, "randomTickSpeed": 3, "spawnRadius": 10, "maxEntityCramming": 24,
}


def to_bool(v) -> bool:
    return str(v).lower() == "true"


def show(kind: str, v) -> str:
    if kind == "bool":
        return "✅" if to_bool(v) else "❌"
    s = str(v)
    return f"`{s[:40]}`" if s else "—"


def current(bot, scope: str, key: str):
    if scope == "prop":
        return props.read(PROPS_FILE).get(key, PROP_DEFAULTS.get(key, ""))
    return bot.cfg["gamerules"].get(key, GR_DEFAULTS.get(key))


def settings_embed(bot, note: Optional[str] = None) -> discord.Embed:
    p = props.read(PROPS_FILE)
    lines = ["**⚙️ Server properties**"]
    for k, (label, kind, _) in PROPS.items():
        lines.append(f"{label}: {show(kind, p.get(k, PROP_DEFAULTS.get(k, '')))}")
    lines.append("\n**🎮 Game rules** *(unset = vanilla default)*")
    gr = bot.cfg["gamerules"]
    for k, (label, kind, _) in GAMERULES.items():
        lines.append(f"{label}: {show(kind, gr.get(k, GR_DEFAULTS[k]))}" + ("" if k in gr else " ·"))
    e = discord.Embed(title="Server settings", description="\n".join(lines)[:4000], colour=0x5865F2)
    if note:
        e.add_field(name="Last change", value=note[:1000], inline=False)
    e.set_footer(text="Pick a setting below · properties apply on next restart (some live) · rules apply live")
    return e


async def apply_setting(bot, scope: str, key: str, value) -> str:
    mc = bot.mc
    v = str(value).lower() if isinstance(value, bool) else str(value)
    if scope == "prop":
        props.write_key(PROPS_FILE, key, v)
        live = {"difficulty": f"difficulty {v}", "gamemode": f"defaultgamemode {v}",
                "white-list": f"whitelist {'on' if v == 'true' else 'off'}"}.get(key)
        if live and mc.state == "online":
            await mc.send(live)
            return "saved and applied live"
        extra = " ⚠️ cracked clients can join!" if key == "online-mode" and v == "false" else ""
        return "saved – restart the server to apply" + extra
    bot.cfg["gamerules"][key] = value
    bot.cfg.save()
    if mc.state == "online":
        await mc.send(f"gamerule {key} {v}")
        return "applied live"
    return "saved – applies when the server starts"


# ───────────────────────────── views ─────────────────────────────
class OpView(discord.ui.View):
    def __init__(self, bot, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.bot.is_op(interaction.user):
            return True
        await interaction.response.send_message("⛔ OP role required.", ephemeral=True)
        return False


class Confirm(OpView):
    def __init__(self, bot, action: Callable[[], Awaitable[str]]):
        super().__init__(bot, 120)
        self.action = action

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="⏳ Working…", embed=None, view=None)
        try:
            msg = await self.action()
        except Exception as e:  # noqa: BLE001
            msg = f"❌ {e}"
        await interaction.edit_original_response(content=msg)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


# ── settings ──
class ChoiceSelect(discord.ui.Select):
    def __init__(self, bot, scope, key, label, choices):
        super().__init__(placeholder=f"Choose {label}", options=[discord.SelectOption(label=c.title(), value=c) for c in choices])
        self.bot, self.scope, self.key, self.label_ = bot, scope, key, label

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0]
        note = await apply_setting(self.bot, self.scope, self.key, v)
        await interaction.response.edit_message(content=f"✅ **{self.label_}** → `{v}` · {note}", view=None)


class ValueModal(discord.ui.Modal):
    def __init__(self, bot, scope, key, table):
        label, kind, extra = table[key]
        super().__init__(title=label[:45])
        self.bot, self.scope, self.key, self.table = bot, scope, key, table
        self.input = discord.ui.TextInput(label=label[:45], default=str(current(bot, scope, key))[:200],
                                          required=(kind == "int"), max_length=200)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        label, kind, extra = self.table[self.key]
        raw = self.input.value.strip()
        if kind == "int":
            try:
                val = int(raw)
            except ValueError:
                return await interaction.response.send_message("❌ Must be a whole number.", ephemeral=True)
            if not extra[0] <= val <= extra[1]:
                return await interaction.response.send_message(f"❌ Must be between {extra[0]} and {extra[1]}.", ephemeral=True)
        else:
            val = raw
            if len(raw) > extra[1]:
                return await interaction.response.send_message(f"❌ Max {extra[1]} characters.", ephemeral=True)
        note = await apply_setting(self.bot, self.scope, self.key, val)
        await interaction.response.send_message(f"✅ **{label}** set to `{val}` · {note}\nPress 🔄 Refresh to update the list.", ephemeral=True)


class SettingSelect(discord.ui.Select):
    def __init__(self, bot, scope, placeholder, table, row):
        super().__init__(placeholder=placeholder, row=row,
                         options=[discord.SelectOption(label=v[0][:100], value=k) for k, v in table.items()])
        self.bot, self.scope, self.table = bot, scope, table

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        label, kind, extra = self.table[key]
        if kind == "bool":
            new = not to_bool(current(self.bot, self.scope, key))
            note = await apply_setting(self.bot, self.scope, key, new)
            await interaction.response.edit_message(
                embed=settings_embed(self.bot, f"**{label}** → {'✅' if new else '❌'} · {note}"),
                view=SettingsView(self.bot))
        elif kind == "choice":
            v = OpView(self.bot)
            v.add_item(ChoiceSelect(self.bot, self.scope, key, label, extra))
            await interaction.response.send_message(f"Choose **{label}**:", view=v, ephemeral=True)
        else:
            await interaction.response.send_modal(ValueModal(self.bot, self.scope, key, self.table))


class SettingsView(OpView):
    def __init__(self, bot):
        super().__init__(bot, 600)
        self.add_item(SettingSelect(bot, "prop", "⚙️ Server settings…", PROPS, 0))
        self.add_item(SettingSelect(bot, "rule", "🎮 Game rules…", GAMERULES, 1))

    @discord.ui.button(label="Refresh", emoji="🔄", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=settings_embed(self.bot), view=SettingsView(self.bot))


# ── plugins ──
class SearchModal(discord.ui.Modal, title="Search plugins"):
    query = discord.ui.TextInput(label="Plugin name", placeholder="e.g. essentials, luckperms, worldedit", max_length=60)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        q = self.query.value.strip()
        try:
            hits = await asyncio.to_thread(installer.mr_search, q, self.bot.cfg["mc_version"])
        except Exception as e:  # noqa: BLE001
            return await interaction.followup.send(f"❌ Search failed: {e}", ephemeral=True)
        if not hits:
            return await interaction.followup.send(f"No plugins found for **{q}**.", ephemeral=True)
        lines = [f"**{i}. {h['title']}** · ⬇️ {h.get('downloads', 0):,}\n{(h.get('description') or '')[:100]}" for i, h in enumerate(hits, 1)]
        e = discord.Embed(title=f"Results for “{q}”", description="\n\n".join(lines)[:4000], colour=0x1BD96A)
        e.set_footer(text="Source: Modrinth · select one to install")
        v = OpView(self.bot)
        v.add_item(InstallSelect(self.bot, hits))
        await interaction.followup.send(embed=e, view=v, ephemeral=True)


class InstallSelect(discord.ui.Select):
    def __init__(self, bot, hits):
        super().__init__(placeholder="Select a plugin to install…", options=[
            discord.SelectOption(label=h["title"][:100], value=h["slug"], description=((h.get("description") or "")[:100] or None))
            for h in hits[:25]])
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        slug = self.values[0]
        await interaction.response.edit_message(content=f"⏳ Installing `{slug}`…", embed=None, view=None)
        try:
            done = await asyncio.to_thread(installer.install_plugin, slug, self.bot.cfg["mc_version"])
        except Exception as e:  # noqa: BLE001
            return await interaction.edit_original_response(content=f"❌ {e}")
        await interaction.edit_original_response(content="✅ Installed:\n" + "\n".join(f"• {d}" for d in done) + "\nRestart the server to load it.")


class RemoveSelect(discord.ui.Select):
    def __init__(self, bot, plugins):
        super().__init__(placeholder="Select plugin(s) to remove…", min_values=1, max_values=len(plugins), options=[
            discord.SelectOption(label=p["title"][:100], value=p["file"], description=p["file"][:100]) for p in plugins])
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        files = list(self.values)

        async def action() -> str:
            for f in files:
                await asyncio.to_thread(installer.remove_plugin, f)
            return f"🗑️ Removed {len(files)} plugin(s). Restart the server to unload them (configs are kept)."

        await interaction.response.send_message("Remove:\n" + "\n".join(f"• `{f}`" for f in files),
                                                view=Confirm(self.bot, action), ephemeral=True)


class PluginMenu(OpView):
    @discord.ui.button(label="Search & install", emoji="🔍", style=discord.ButtonStyle.success)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SearchModal(self.bot))

    @discord.ui.button(label="Installed / remove", emoji="📦", style=discord.ButtonStyle.primary)
    async def installed(self, interaction: discord.Interaction, button: discord.ui.Button):
        plugins = installer.list_plugins()
        if not plugins:
            return await interaction.response.send_message("No plugins installed.", ephemeral=True)
        lines = "\n".join(f"• **{p['title']}** `{p['version']}` – `{p['file']}`" for p in plugins)[:3800]
        e = discord.Embed(title=f"Installed plugins ({len(plugins)})", description=lines, colour=0x5865F2)
        if len(plugins) > 25:
            e.set_footer(text="Showing the first 25 in the remove menu")
        v = OpView(self.bot)
        v.add_item(RemoveSelect(self.bot, plugins[:25]))
        await interaction.response.send_message(embed=e, view=v, ephemeral=True)

    @discord.ui.button(label="Update all", emoji="⬆️")
    async def update(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        updated = await asyncio.to_thread(installer.update_all, self.bot.cfg["mc_version"])
        msg = ("✅ Updated: " + ", ".join(updated) + "\nRestart the server to apply.") if updated else "Everything is up to date."
        await interaction.followup.send(msg, ephemeral=True)


async def read_square_png(file: discord.Attachment, size: int) -> bytes:
    """Download an attachment, center-crop to a square and return PNG bytes."""
    if file.size > 8 * 1024 * 1024:
        raise ValueError("Image too large (max 8 MB).")
    data = await file.read()

    def work() -> bytes:
        from PIL import Image
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as e:  # noqa: BLE001
            raise ValueError("That doesn't look like a valid image (png/jpg/gif/webp).") from e
        im = im.convert("RGBA")
        w, h = im.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        im = im.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()

    return await asyncio.to_thread(work)


async def read_square_png_from_path(path, size: int) -> bytes:
    def work() -> bytes:
        from PIL import Image
        im = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    return await asyncio.to_thread(work)


def fmt_size(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


# ───────────────────────────── help ─────────────────────────────
def help_pages(bot) -> dict:
    cfg = bot.cfg
    return {
        "start": ("🚀 Getting started", 0x57F287, [
            ("The panel", f"In <#{cfg['panel_channel_id']}> press **▶️ Start** (anyone). **⏹️ Stop** and **🔄 Restart** need the OP role."),
            ("Commands", f"Type `/` in <#{cfg['configure_channel_id']}> (OP role only). Most changes need a restart: `/server restart`."),
            ("Console", f"Type a Minecraft command in <#{cfg['console_channel_id']}> (without `/`) to run it. OP role only."),
            ("Cross-chat", f"<#{cfg['crosschat_channel_id']}> is Minecraft ⇄ Discord chat, handled by the DiscordSRV plugin."),
            ("Commands missing?", "Press **Ctrl+R** in Discord, or mention me with `@bot sync`. `@bot invite` gives the invite link."),
        ]),
        "server": ("🖥️ Server", 0x5865F2, [
            ("/server `action`", "start · stop · restart · kill (kill = force stop)."),
            ("/autoshut `[enabled]` `[minutes]`", "Stops the server when nobody is online. `/autoshut` alone toggles it. Default 3 min."),
            ("/autostart `[enabled]`", "Start the server automatically when someone tries to join while it's offline (Java). Toggles if empty."),
            ("/autorestart `hours`", "Restart every N hours (0 = off)."),
            ("/ram `amount`", "e.g. `/ram 4G`. Applies on next start."),
            ("/version `[version]`", "Show or change the Minecraft version (server must be offline, backup is made first)."),
            ("/motd `text`", "Server-list text. `&a` = colours, `\\n` = second line."),
            ("/icon `[image]`", "Server-list picture (auto 64×64). No image = default."),
        ]),
        "plugins": ("🧩 Plugins", 0x1BD96A, [
            ("/plugin", "Menu: 🔍 search & install from Modrinth · 📦 installed / remove · ⬆️ update all."),
            ("/plugin_upload `file`", "Upload your own `.jar` plugin."),
            ("Restart needed", "Installed or removed plugins load after `/server restart`."),
        ]),
        "settings": ("⚙️ Settings", 0xFEE75C, [
            ("/settings", "Pick a setting from the menus: difficulty, gamemode, PvP, max players, view distance, whitelist…"),
            ("Game rules", "Keep inventory, mob griefing, sleeping %, daylight cycle… applied live and on every start."),
            ("Properties", "Most apply after a restart. Difficulty, default gamemode and whitelist apply live."),
        ]),
        "world": ("🌍 World & backups", 0xE67E22, [
            ("/world upload `file` or `url`", "Replace the world with a `.zip` (server offline). Old world is backed up first."),
            ("/world reset", "Delete the world and generate a new one (backup first)."),
            ("/backup create · list", "Create a backup (works while online) / list backups."),
            ("/backup restore · delete `name`", "Restore (server offline) or delete a backup."),
            ("/backup schedule `hours` `[keep]`", "Automatic backups while online (0 = off)."),
        ]),
        "players": ("👥 Players", 0xEB459E, [
            ("/players", "Who is online."),
            ("/whitelist `action` `[player]`", "add · remove · list · on · off. Bedrock names start with `.`"),
            ("/op · /deop `player`", "Give / remove in-game operator."),
            ("/kick · /ban `player` `[reason]`", "Kick or ban."),
            ("/pardon `player`", "Unban."),
            ("/command `command`", "Run any console command and see the output."),
        ]),
    }


def help_embed(bot, key: str) -> discord.Embed:
    title, colour, fields = help_pages(bot)[key]
    e = discord.Embed(title=title, colour=colour)
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.set_footer(text="Use the menu below to browse topics")
    return e


class HelpSelect(discord.ui.Select):
    def __init__(self, bot):
        super().__init__(placeholder="📖 Choose a topic…", options=[
            discord.SelectOption(label=v[0][2:].strip(), value=k, emoji=v[0][0]) for k, v in help_pages(bot).items()])
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=help_embed(self.bot, self.values[0]), view=HelpView(self.bot))


class HelpView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=600)
        self.add_item(HelpSelect(bot))


# ───────────────────────────── cog ─────────────────────────────
class Configure(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Easy guide to every command and feature")
    async def help_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=help_embed(self.bot, "start"), view=HelpView(self.bot), ephemeral=True)

    # helpers
    async def _cmd(self, interaction: discord.Interaction, command: str, wait: float = 1.5):
        await interaction.response.defer(ephemeral=True)
        if self.bot.mc.state != "online":
            return await interaction.followup.send("⚠️ The server must be online for this.", ephemeral=True)
        lines = await self.bot.mc.run(command, wait)
        out = "\n".join(lines)[:1700].replace("```", "'''") or "(no output)"
        await interaction.followup.send(f"`/{command[:100]}`\n```\n{out}\n```", ephemeral=True)

    @staticmethod
    def _name_ok(name: str) -> bool:
        return bool(NAME_RE.match(name))

    # ── server control ──
    @app_commands.command(name="server", description="Start, stop, restart or kill the Minecraft server")
    async def server_cmd(self, interaction: discord.Interaction, action: Literal["start", "stop", "restart", "kill"]):
        await interaction.response.defer(ephemeral=True)
        mc = self.bot.mc
        ok, msg = await {"start": mc.start, "stop": mc.stop, "restart": mc.restart, "kill": mc.kill}[action]()
        await interaction.followup.send(("✅ " if ok else "⚠️ ") + msg, ephemeral=True)

    # ── auto shutdown / restart ──
    @app_commands.command(name="autoshut", description="Toggle auto-shutdown when nobody is online")
    @app_commands.describe(enabled="Turn auto-shutdown on/off", minutes="Idle minutes before shutdown (default 3)")
    async def autoshut(self, interaction: discord.Interaction, enabled: Optional[bool] = None,
                       minutes: Optional[app_commands.Range[int, 1, 240]] = None):
        a = self.bot.cfg["autoshut"]
        if enabled is None and minutes is None:
            enabled = not a["enabled"]  # plain /autoshut toggles
        if enabled is not None:
            a["enabled"] = enabled
        if minutes is not None:
            a["minutes"] = minutes
        self.bot.cfg.save()
        self.bot._dirty = True
        await interaction.response.send_message(
            f"💤 Auto-shutdown is **{'ON' if a['enabled'] else 'OFF'}** – stops the server after **{a['minutes']} min** with no players.",
            ephemeral=True)

    @app_commands.command(name="autostart", description="Toggle: start the server automatically when someone tries to join")
    @app_commands.describe(enabled="On/off – leave empty to toggle")
    async def autostart(self, interaction: discord.Interaction, enabled: Optional[bool] = None):
        cfg = self.bot.cfg
        cfg["autostart"] = (not cfg["autostart"]) if enabled is None else enabled
        cfg.save()
        await interaction.response.defer(ephemeral=True)
        await self.bot.wake.sync()
        self.bot._dirty = True
        icon = "🔔" if cfg["autostart"] else "🔕"
        await interaction.followup.send(
            f"{icon} Auto-start on join is **{'ON' if cfg['autostart'] else 'OFF'}**.\n`{self.bot.wake.status_text()}`\n"
            "*When the server is offline, joining shows “starting – reconnect in a minute” and boots it. "
            "Server-list pings never start it. If the whitelist is on, only whitelisted names can wake it. Java Edition only.*",
            ephemeral=True)

    @app_commands.command(name="autorestart", description="Restart the server every N hours (0 = off)")
    async def autorestart(self, interaction: discord.Interaction, hours: app_commands.Range[int, 0, 168]):
        self.bot.cfg["autorestart_hours"] = hours
        self.bot.cfg.save()
        await interaction.response.send_message(f"🔁 Auto-restart: **{'every ' + str(hours) + 'h' if hours else 'off'}**.", ephemeral=True)

    # ── plugins ──
    @app_commands.command(name="plugin", description="Search, install, update and remove plugins")
    async def plugin(self, interaction: discord.Interaction):
        n = len(installer.list_plugins())
        e = discord.Embed(title="🧩 Plugin manager", colour=0x1BD96A,
                          description=f"**{n}** plugin(s) installed · Minecraft `{self.bot.cfg['mc_version']}`\n"
                                      "Plugins come from Modrinth. Restart the server after changes.")
        await interaction.response.send_message(embed=e, view=PluginMenu(self.bot), ephemeral=True)

    @app_commands.command(name="plugin_upload", description="Upload your own plugin .jar")
    async def plugin_upload(self, interaction: discord.Interaction, file: discord.Attachment):
        if not file.filename.endswith(".jar") or "/" in file.filename or "\\" in file.filename:
            return await interaction.response.send_message("❌ Please attach a .jar file.", ephemeral=True)
        if file.size > 100 * 1024 * 1024:
            return await interaction.response.send_message("❌ File too large (max 100 MB).", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        installer.PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        await file.save(installer.PLUGIN_DIR / file.filename)
        await interaction.followup.send(f"✅ Uploaded `{file.filename}`. Restart the server to load it.", ephemeral=True)

    # ── settings ──
    @app_commands.command(name="settings", description="All server settings, game rules and more")
    async def settings(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=settings_embed(self.bot), view=SettingsView(self.bot), ephemeral=True)

    @app_commands.command(name="ram", description="Set the server's RAM (e.g. 4G or 4096M)")
    async def ram(self, interaction: discord.Interaction, amount: str):
        try:
            mb = parse_ram(amount)
        except ValueError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        total = psutil.virtual_memory().total // 1048576
        if mb > total * 0.9:
            return await interaction.response.send_message(f"❌ That is too much – this machine has {total} MB.", ephemeral=True)
        self.bot.cfg["ram_mb"] = mb
        self.bot.cfg.save()
        await interaction.response.send_message(f"💾 RAM set to **{mb} MB** (machine has {total} MB). Restart the server to apply.", ephemeral=True)

    # ── version ──
    @app_commands.command(name="version", description="Show or change the Minecraft (Paper) version")
    @app_commands.describe(version="Version to switch to (server must be offline)")
    async def version(self, interaction: discord.Interaction, version: Optional[str] = None):
        bot, mc = self.bot, self.bot.mc
        if not bot.versions:
            await interaction.response.defer(ephemeral=True)
            try:
                bot.versions = await asyncio.to_thread(installer.paper_versions)
            except Exception as e:  # noqa: BLE001
                return await interaction.followup.send(f"❌ Could not reach PaperMC: {e}", ephemeral=True)
            send = interaction.followup.send
        else:
            send = interaction.response.send_message
        cur = bot.cfg["mc_version"]
        if not version:
            return await send(f"Running **Paper {cur}** · latest: `{bot.versions[0]}`\nUse `/version version:<x>` to switch.", ephemeral=True)
        if version not in bot.versions:
            return await send("❌ Unknown version – pick one from the autocomplete list.", ephemeral=True)
        if mc.state != "offline":
            return await send("⚠️ Stop the server first.", ephemeral=True)

        async def action() -> str:
            await mc.backup("pre-upgrade")
            await asyncio.to_thread(installer.download_server, version)
            bot.cfg["mc_version"] = version
            bot.cfg.save()
            updated = await asyncio.to_thread(installer.update_all, version)
            return f"✅ Now on **Paper {version}** ({len(updated)} plugin(s) updated). A backup was made first. Press Start in the panel."

        warn = "\n⚠️ **Downgrading can corrupt worlds.**" if installer.vkey(version) < installer.vkey(cur or "0") else ""
        await send(f"Switch **{cur} → {version}**? A backup is created first.{warn}", view=Confirm(bot, action), ephemeral=True)

    @version.autocomplete("version")
    async def version_ac(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=v, value=v) for v in self.bot.versions if current in v][:25]

    # ── backups ──
    backup = app_commands.Group(name="backup", description="World backups")

    @backup.command(name="create", description="Create a backup now (works while online)")
    async def backup_create(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        path = await self.bot.mc.backup()
        await interaction.followup.send(f"💾 Backup `{path.name}` created ({fmt_size(path.stat().st_size)}).", ephemeral=True)

    @backup.command(name="list", description="List backups")
    async def backup_list(self, interaction: discord.Interaction):
        items = backups.list_backups()
        if not items:
            return await interaction.response.send_message("No backups yet.", ephemeral=True)
        lines = [f"`{b['name']}` · {fmt_size(b['size'])} · <t:{int(b['mtime'])}:R>" for b in items[:25]]
        await interaction.response.send_message(embed=discord.Embed(title="💾 Backups", description="\n".join(lines), colour=0x5865F2), ephemeral=True)

    async def _backup_ac(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=b["name"], value=b["name"]) for b in backups.list_backups() if current in b["name"]][:25]

    @backup.command(name="restore", description="Restore a backup (server must be offline)")
    async def backup_restore(self, interaction: discord.Interaction, name: str):
        mc = self.bot.mc
        if mc.state != "offline":
            return await interaction.response.send_message("⚠️ Stop the server first.", ephemeral=True)

        async def action() -> str:
            await mc.backup("pre-restore")
            await asyncio.to_thread(backups.restore, name)
            return f"✅ Restored `{name}`. (Your previous world was saved as a `pre-restore` backup.)"

        await interaction.response.send_message(f"Restore `{name}`? The current world will be replaced.", view=Confirm(self.bot, action), ephemeral=True)

    @backup.command(name="delete", description="Delete a backup")
    async def backup_delete(self, interaction: discord.Interaction, name: str):
        try:
            backups.delete_backup(name)
        except Exception as e:  # noqa: BLE001
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        await interaction.response.send_message(f"🗑️ Deleted `{name}`.", ephemeral=True)

    @backup.command(name="schedule", description="Automatic backups every N hours while online (0 = off)")
    async def backup_schedule(self, interaction: discord.Interaction, hours: app_commands.Range[int, 0, 168],
                              keep: Optional[app_commands.Range[int, 1, 100]] = None):
        self.bot.cfg["backup_hours"] = hours
        if keep:
            self.bot.cfg["max_backups"] = keep
        self.bot.cfg.save()
        await interaction.response.send_message(
            f"💾 Auto-backup: **{'every ' + str(hours) + 'h' if hours else 'off'}** · keeping the newest **{self.bot.cfg['max_backups']}**.", ephemeral=True)

    backup_restore.autocomplete("name")(_backup_ac)
    backup_delete.autocomplete("name")(_backup_ac)

    # ── server list look: MOTD + icon ──
    @app_commands.command(name="motd", description="Set the text shown in the Minecraft server list")
    @app_commands.describe(text="Colours with &: &a green, &l bold, &r reset. Use \\n for a second line.")
    async def motd(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 120]):
        props.write_key(PROPS_FILE, "motd", props.motd_value(text), raw=True)
        note = "Applies after the next restart." if self.bot.mc.state != "offline" else "Applies on next start."
        await interaction.response.send_message(f"✅ Server list MOTD set. {note}", ephemeral=True)

    @app_commands.command(name="icon", description="Set the picture shown in the Minecraft server list (no image = default)")
    async def icon(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None):
        await interaction.response.defer(ephemeral=True)
        SERVER_DIR.mkdir(parents=True, exist_ok=True)
        target = SERVER_DIR / "server-icon.png"
        try:
            if image is None:
                if not DEFAULT_ICON.exists():
                    target.unlink(missing_ok=True)
                    return await interaction.followup.send("✅ Icon removed.", ephemeral=True)
                png = await read_square_png_from_path(DEFAULT_ICON, 64)
            else:
                png = await read_square_png(image, 64)
        except ValueError as e:
            return await interaction.followup.send(f"❌ {e}", ephemeral=True)
        target.write_bytes(png)
        note = "Restart the server to show it in the server list." if self.bot.mc.state != "offline" else "It shows on next start."
        await interaction.followup.send(f"✅ Server icon updated (cropped to 64×64). {note}", ephemeral=True)

    # ── world ──
    world = app_commands.Group(name="world", description="Replace or reset the world")

    @world.command(name="upload", description="Replace the world with a .zip (attach it or paste a direct link)")
    @app_commands.describe(file="A .zip of the world folder", url="…or a direct download link (for big worlds)")
    async def world_upload(self, interaction: discord.Interaction, file: Optional[discord.Attachment] = None, url: Optional[str] = None):
        mc = self.bot.mc
        if (file is None) == (url is None):
            return await interaction.response.send_message("❌ Provide either a `file` or a `url` (not both).", ephemeral=True)
        if mc.state != "offline":
            return await interaction.response.send_message("⚠️ Stop the server first.", ephemeral=True)
        if file and not file.filename.lower().endswith(".zip"):
            return await interaction.response.send_message("❌ The world must be a .zip file.", ephemeral=True)
        if url and not url.startswith(("http://", "https://")):
            return await interaction.response.send_message("❌ The link must start with http:// or https://", ephemeral=True)

        async def action() -> str:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            tmp = BACKUP_DIR / "world-upload.tmp.zip"
            try:
                if file:
                    await file.save(tmp)
                else:
                    await asyncio.to_thread(installer.download, url, tmp, None, 5 * 1024 ** 3)
                if backups.world_dirs():
                    await mc.backup("pre-upload")
                summary = await asyncio.to_thread(backups.import_world, tmp)
            finally:
                tmp.unlink(missing_ok=True)
            return f"✅ World replaced ({summary}). Your old world was saved as a `pre-upload` backup. Press Start in the panel.\n" \
                   "*If the world came from a newer Minecraft version, change the server version with `/version` first.*"

        await interaction.response.send_message("⚠️ **Replace the current world with this upload?** A backup is made first.",
                                                view=Confirm(self.bot, action), ephemeral=True)

    @world.command(name="reset", description="Delete the world(s) and generate a fresh one (server offline)")
    async def world_reset(self, interaction: discord.Interaction):
        mc = self.bot.mc
        if mc.state != "offline":
            return await interaction.response.send_message("⚠️ Stop the server first.", ephemeral=True)
        if not backups.world_dirs():
            return await interaction.response.send_message("There is no world to reset.", ephemeral=True)

        async def action() -> str:
            await mc.backup("pre-reset")
            await asyncio.to_thread(backups.delete_worlds)
            return "✅ World deleted (a `pre-reset` backup was saved). A new world generates on next start."

        await interaction.response.send_message("⚠️ **Delete the world?** A backup is made first. Set a seed in `/settings` beforehand if you want one.",
                                                view=Confirm(self.bot, action), ephemeral=True)

    # ── players & moderation ──
    @app_commands.command(name="players", description="Show who is online")
    async def players(self, interaction: discord.Interaction):
        await self._cmd(interaction, "list")

    @app_commands.command(name="command", description="Run any console command")
    async def command(self, interaction: discord.Interaction, command: str):
        await self._cmd(interaction, command.lstrip("/"))

    @app_commands.command(name="whitelist", description="Manage the whitelist (Bedrock names start with '.')")
    async def whitelist(self, interaction: discord.Interaction, action: Literal["add", "remove", "list", "on", "off"], player: Optional[str] = None):
        if action in ("add", "remove"):
            if not player or not self._name_ok(player):
                return await interaction.response.send_message("❌ Give a valid player name.", ephemeral=True)
            if player.startswith("."):  # Floodgate (Bedrock) player
                return await self._cmd(interaction, f"fwhitelist {action} {player[1:]}")
            return await self._cmd(interaction, f"whitelist {action} {player}")
        await self._cmd(interaction, f"whitelist {action}")

    async def _player_cmd(self, interaction: discord.Interaction, verb: str, player: str, reason: str = ""):
        if not self._name_ok(player):
            return await interaction.response.send_message("❌ Invalid player name.", ephemeral=True)
        await self._cmd(interaction, f"{verb} {player} {reason}".strip())

    @app_commands.command(name="op", description="Give a player operator status in-game")
    async def op(self, interaction: discord.Interaction, player: str):
        await self._player_cmd(interaction, "op", player)

    @app_commands.command(name="deop", description="Remove in-game operator status")
    async def deop(self, interaction: discord.Interaction, player: str):
        await self._player_cmd(interaction, "deop", player)

    @app_commands.command(name="kick", description="Kick a player")
    async def kick(self, interaction: discord.Interaction, player: str, reason: str = ""):
        await self._player_cmd(interaction, "kick", player, reason)

    @app_commands.command(name="ban", description="Ban a player")
    async def ban(self, interaction: discord.Interaction, player: str, reason: str = ""):
        await self._player_cmd(interaction, "ban", player, reason)

    @app_commands.command(name="pardon", description="Unban a player")
    async def pardon(self, interaction: discord.Interaction, player: str):
        await self._player_cmd(interaction, "pardon", player)
