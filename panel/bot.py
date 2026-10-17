from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import installer
from .config import Config
from .configure import Configure
from .server import ServerManager
from .wake import WakeListener

log = logging.getLogger("panel.bot")

STATE_STYLE = {
    "offline": ("🔴", "Offline", 0xED4245),
    "starting": ("🟡", "Starting…", 0xFEE75C),
    "online": ("🟢", "Online", 0x57F287),
    "stopping": ("🟠", "Stopping…", 0xE67E22),
}
NO_PINGS = discord.AllowedMentions.none()
PANEL_TITLE = "⛏️  Minecraft Server"
STATE_HINT = {
    "offline": "Press **▶️ Start** to boot the server.",
    "starting": "⏳ Booting up – usually about a minute…",
    "online": "✨ Ready to play!",
    "stopping": "💾 Saving the world and shutting down…",
}


def fmt_uptime(seconds: float) -> str:
    """Coarse on purpose: 'Just now', '1 min', '5 mins', '1 hr', '2 hrs 5 min', '1 d 3 hr'."""
    m = int(seconds // 60)
    if m < 1:
        return "Just now"
    if m < 60:
        return f"{m} min" if m == 1 else f"{m} mins"
    h, m = divmod(m, 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d} d" + (f" {h} hr" if h else "")
    return (f"{h} hr" if h == 1 else f"{h} hrs") + (f" {m} min" if m else "")


class PanelTree(app_commands.CommandTree):
    """All slash commands: configure channel + OP role only."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: PanelBot = interaction.client  # type: ignore[assignment]
        if interaction.command is not None and interaction.command.name == "help":
            return True
        if interaction.channel_id != bot.cfg["configure_channel_id"]:
            await interaction.response.send_message(f"Use my commands in <#{bot.cfg['configure_channel_id']}>.", ephemeral=True)
            return False
        if not bot.is_op(interaction.user):
            await interaction.response.send_message("⛔ You need the OP role to use this.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        err = getattr(error, "original", error)
        log.error("command error: %r", err, exc_info=err)
        msg = f"❌ {err}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


class PanelView(discord.ui.View):
    def __init__(self, bot: "PanelBot"):
        super().__init__(timeout=None)
        self.bot = bot
        st = bot.mc.state
        self.btn_start.disabled = st != "offline"
        self.btn_stop.disabled = st in ("offline", "stopping")
        self.btn_restart.disabled = st != "online"

    async def _run(self, interaction: discord.Interaction, action) -> None:
        # Acknowledge silently (no "thinking" bubble, no ephemeral clutter): the panel message edits itself instantly.
        await interaction.response.defer()
        ok, msg = await action()
        if not ok:
            await interaction.followup.send("⚠️ " + msg, ephemeral=True)

    @discord.ui.button(label="Start", emoji="▶️", style=discord.ButtonStyle.success, custom_id="panel:start")
    async def btn_start(self, interaction: discord.Interaction, button: discord.ui.Button):
        log.info("%s pressed Start", interaction.user)
        await self._run(interaction, self.bot.mc.start)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="panel:stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.bot.is_op(interaction.user):
            return await interaction.response.send_message("⛔ Only the OP role can stop the server.", ephemeral=True)
        log.info("%s pressed Stop", interaction.user)
        await self._run(interaction, self.bot.mc.stop)

    @discord.ui.button(label="Restart", emoji="🔄", style=discord.ButtonStyle.primary, custom_id="panel:restart")
    async def btn_restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.bot.is_op(interaction.user):
            return await interaction.response.send_message("⛔ Only the OP role can restart the server.", ephemeral=True)
        log.info("%s pressed Restart", interaction.user)
        await self._run(interaction, self.bot.mc.restart)


class PanelBot(commands.Bot):
    """Panel + console + slash commands. Cross-chat is handled by the DiscordSRV plugin."""

    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         tree_cls=PanelTree, help_command=None)
        self.cfg = cfg
        self.mc = ServerManager(cfg)
        self.wake = WakeListener(self)
        self.versions: list = []
        self.panel_msg: Optional[discord.Message] = None
        self.console_q: deque = deque(maxlen=600)
        self._panel_lock = asyncio.Lock()
        self._last_embed: Optional[dict] = None
        self._last_edit = 0.0
        self._dirty = False
        self._tick = 0
        self._ready_once = False
        self._restarting = False
        self._backing_up = False

    # ── helpers ─────────────────────────────────────────────
    def is_op(self, user) -> bool:
        return isinstance(user, discord.Member) and any(r.id == self.cfg["op_role_id"] for r in user.roles)

    async def chan(self, key: str):
        cid = self.cfg[key]
        return self.get_channel(cid) or await self.fetch_channel(cid)

    async def send_console(self, text: str) -> None:
        try:
            await (await self.chan("console_channel_id")).send(text[:1900], allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            pass

    # ── startup ─────────────────────────────────────────────
    async def setup_hook(self) -> None:
        self.add_view(PanelView(self))
        await self.add_cog(Configure(self))
        mc = self.mc
        mc.on("line", lambda l: self.console_q.append(l))
        mc.on("join", lambda n: self.request_update())
        mc.on("leave", lambda n: self.request_update())
        mc.pre_start.append(self.wake.close)
        mc.on("state", self._on_state)
        mc.on("exit", self._on_exit)

    def invite_url(self) -> str:
        perms = discord.Permissions(view_channel=True, send_messages=True, embed_links=True, read_message_history=True,
                                    add_reactions=True, attach_files=True, manage_webhooks=True)
        return discord.utils.oauth_url(self.user.id, permissions=perms, scopes=("bot", "applications.commands"))

    async def sync_commands(self) -> str:
        """Register slash commands instantly in the server(s). Does NOT need access to the configure channel."""
        ch = self.get_channel(self.cfg["configure_channel_id"])
        guilds = [ch.guild] if ch is not None and getattr(ch, "guild", None) else list(self.guilds)
        if not guilds:
            raise RuntimeError("The bot is not in any server – invite it with the link printed in the log.")
        done = []
        for g in guilds:
            self.tree.copy_global_to(guild=g)
            synced = await self.tree.sync(guild=g)
            done.append(f"{g.name} ({len(synced)} commands)")
        return ", ".join(done)

    def check_channels(self) -> None:
        """Log clear warnings when the bot cannot see/use one of the configured channels."""
        for key, label in (("panel_channel_id", "panel"), ("crosschat_channel_id", "cross-chat"),
                           ("console_channel_id", "console"), ("configure_channel_id", "configure")):
            ch = self.get_channel(self.cfg[key])
            if ch is None:
                log.warning("Cannot see the %s channel (%s) – give the bot's role 'View Channel' there.", label, self.cfg[key])
                continue
            perms = ch.permissions_for(ch.guild.me)
            need = {"view_channel": "View Channel", "send_messages": "Send Messages",
                    "embed_links": "Embed Links", "read_message_history": "Read Message History"}
            missing = [n for k, n in need.items() if not getattr(perms, k)]
            if key == "crosschat_channel_id" and not perms.manage_webhooks:
                missing.append("Manage Webhooks (for player-head chat)")
            if missing:
                log.warning("Missing permissions in the %s channel: %s", label, ", ".join(missing))

    async def on_ready(self) -> None:
        if self._ready_once:
            return
        self._ready_once = True
        log.info("Logged in as %s", self.user)
        log.info("Invite link (use this if slash commands don't appear): %s", self.invite_url())
        self.check_channels()
        try:
            log.info("Slash commands synced: %s", await self.sync_commands())
        except discord.Forbidden:
            log.error("Discord refused to register slash commands (Missing Access). The bot was invited WITHOUT the "
                      "'applications.commands' scope. Re-invite it with this link (no need to kick it first):\n%s", self.invite_url())
        except Exception:  # noqa: BLE001
            log.exception("Slash command sync failed")
        await self._init_panel()
        await self.wake.sync()
        self.panel_loop.start()
        self.console_loop.start()
        self.maintenance.start()
        asyncio.ensure_future(self._load_versions())

    async def _load_versions(self) -> None:
        try:
            self.versions = await asyncio.to_thread(installer.paper_versions)
        except Exception:  # noqa: BLE001
            log.warning("Could not load Paper version list")

    async def _init_panel(self) -> None:
        """Find the ONE existing panel message and reuse it. A new one is only posted if none exists."""
        try:
            ch = await self.chan("panel_channel_id")
        except discord.HTTPException as e:
            log.error("Cannot access the panel channel (%s): %s – give the bot View Channel + Send Messages there.", self.cfg["panel_channel_id"], e)
            return
        msg: Optional[discord.Message] = None
        mid = self.cfg["panel_message_id"]
        if mid:
            try:
                msg = await ch.fetch_message(mid)
            except discord.NotFound:
                log.info("Stored panel message %s no longer exists", mid)
            except discord.Forbidden:
                log.warning("Cannot read the panel channel history – give the bot 'Read Message History' (otherwise it can't find its panel).")
            except discord.HTTPException:
                log.exception("Could not fetch the stored panel message")
        found: list = []  # panels we posted earlier (newest first) – e.g. if the id was lost
        try:
            async for m in ch.history(limit=100):
                if m.author.id == self.user.id and m.embeds and m.embeds[0].title == PANEL_TITLE:
                    found.append(m)
        except discord.HTTPException:
            pass
        if msg is None and found:
            msg = found[0]
            log.info("Re-using existing panel message %s", msg.id)
        for extra in found:  # keep exactly one panel
            if msg is not None and extra.id != msg.id:
                try:
                    await extra.delete()
                    log.info("Removed duplicate panel message %s", extra.id)
                except discord.HTTPException:
                    pass
        self.panel_msg = msg
        if msg is not None and self.cfg["panel_message_id"] != msg.id:
            self.cfg["panel_message_id"] = msg.id
            self.cfg.save()
        await self.update_panel(force=True)

    # ── panel (minimal: status, player count, version, uptime) ──
    def build_embed(self) -> discord.Embed:
        mc, cfg = self.mc, self.cfg
        emoji, label, color = STATE_STYLE[mc.state]
        e = discord.Embed(title=PANEL_TITLE, description=STATE_HINT[mc.state], colour=color)
        e.add_field(name="Status", value=f"{emoji} **{label}**")
        e.add_field(name="Players", value=f"👥 **{len(mc.players)}** / {mc.max_players()}")
        e.add_field(name="Version", value=f"🧩 Paper `{cfg['mc_version'] or '?'}`")
        if mc.state == "online" and mc.online_at:
            e.add_field(name="Uptime", value=f"⏱️ {fmt_uptime(time.time() - mc.online_at)}")
        a = cfg["autoshut"]
        bits = [f"💤 Auto-shutdown {a['minutes']} min" if a["enabled"] else "💤 Auto-shutdown off",
                "🔔 Auto-start on join" if cfg["autostart"] else "🔔 Auto-start off"]
        e.set_footer(text="  ·  ".join(bits))
        return e

    def request_update(self) -> None:
        """Refresh the panel now (max one edit per 2 s; extra requests are merged by the loop)."""
        if time.monotonic() - self._last_edit >= 2:
            asyncio.ensure_future(self.update_panel())
        else:
            self._dirty = True

    async def update_panel(self, force: bool = False) -> None:
        async with self._panel_lock:
            embed = self.build_embed()
            d = embed.to_dict()
            if not force and d == self._last_embed:
                return  # nothing visible changed -> no API call
            view = PanelView(self)
            try:
                if self.panel_msg is not None:
                    try:
                        await self.panel_msg.edit(embed=embed, view=view, attachments=[])
                    except discord.NotFound:
                        log.warning("Panel message was deleted – posting a new one")
                        self.panel_msg = None
                if self.panel_msg is None:
                    self.panel_msg = await (await self.chan("panel_channel_id")).send(embed=embed, view=view)
                    self.cfg["panel_message_id"] = self.panel_msg.id
                    self.cfg.save()
                    log.info("Posted panel message %s", self.panel_msg.id)
                self._last_embed = d
                self._last_edit = time.monotonic()
            except discord.HTTPException:
                log.exception("panel update failed")

    @tasks.loop(seconds=2)
    async def panel_loop(self) -> None:
        try:
            self._tick += 1
            # Edits only happen when the visible text changes (uptime is minute-granular, checked every 30 s).
            if self._dirty or self._tick % 15 == 0:
                self._dirty = False
                await self.update_panel()
        except Exception:  # noqa: BLE001
            log.exception("panel loop")

    async def _on_state(self, state: str) -> None:
        await self.update_panel(force=True)
        await self.wake.sync()

    async def _on_exit(self, code: int, crashed: bool) -> None:
        if crashed:
            await self.send_console(f"⚠️ **Server crashed** (exit code `{code}`). Check `server/logs/latest.log`.")

    # ── console channel: OP messages run as commands ────────
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if self.user and message.content.startswith((f"<@{self.user.id}>", f"<@!{self.user.id}>")) and self.is_op(message.author):
            word = message.content.split(">", 1)[1].strip().lower()
            if word == "sync":
                try:
                    return await message.reply(f"✅ Slash commands synced: {await self.sync_commands()}\nPress **Ctrl+R** (or restart Discord) if they don't show yet.", mention_author=False)
                except discord.Forbidden:
                    return await message.reply(f"❌ Missing Access – re-invite me with the `applications.commands` scope:\n<{self.invite_url()}>", mention_author=False)
                except Exception as e:  # noqa: BLE001
                    return await message.reply(f"❌ Sync failed: {e}", mention_author=False)
            if word == "invite":
                return await message.reply(f"<{self.invite_url()}>", mention_author=False)
        if message.channel.id != self.cfg["console_channel_id"] or not self.is_op(message.author):
            return
        cmd = message.content.strip().split("\n")[0].lstrip("/").strip()
        if not cmd:
            return
        if self.mc.state == "offline":
            return await message.reply("⚠️ Server is offline. Use the panel to start it.", mention_author=False)
        try:
            await self.mc.send(cmd)
            await message.add_reaction("✅")
        except Exception as e:  # noqa: BLE001
            await message.reply(f"❌ {e}", mention_author=False)

    @tasks.loop(seconds=2)
    async def console_loop(self) -> None:
        try:
            if not self.console_q:
                return
            lines = []
            while self.console_q:
                lines.append(self.console_q.popleft()[:1500].replace("```", "'''"))
            chunks, cur = [], ""
            for ln in lines:
                if len(cur) + len(ln) + 1 > 1850:
                    chunks.append(cur)
                    cur = ""
                cur += ln + "\n"
            if cur:
                chunks.append(cur)
            skipped = 0
            if len(chunks) > 5:
                skipped, chunks = len(chunks) - 5, chunks[-5:]
            ch = await self.chan("console_channel_id")
            if skipped:
                await ch.send(f"*…skipped {skipped} chunk(s) of output (see server/logs/latest.log)*")
            for c in chunks:
                await ch.send(f"```\n{c}```", allowed_mentions=NO_PINGS)
        except Exception:  # noqa: BLE001
            log.exception("console loop")

    # ── background maintenance ──────────────────────────────
    @tasks.loop(seconds=15)
    async def maintenance(self) -> None:
        try:
            mc, cfg = self.mc, self.cfg
            if mc.state != "online":
                return
            a = cfg["autoshut"]
            if a["enabled"] and not mc.players and mc.empty_since and time.time() - mc.empty_since >= a["minutes"] * 60:
                await self.send_console(f"💤 No players for {a['minutes']} min – shutting the server down to save resources.")
                await mc.stop()
                return
            hrs = cfg["autorestart_hours"]
            if hrs and mc.online_at and not self._restarting and time.time() - mc.online_at >= hrs * 3600:
                asyncio.ensure_future(self._auto_restart())
            bh = cfg["backup_hours"]
            if bh and not self._backing_up and time.time() - cfg["last_backup"] >= bh * 3600:
                asyncio.ensure_future(self._scheduled_backup())
        except Exception:  # noqa: BLE001
            log.exception("maintenance")

    async def _auto_restart(self) -> None:
        self._restarting = True
        try:
            await self.send_console("🔁 Scheduled restart in 60 seconds.")
            await self.mc.send("say Scheduled restart in 60 seconds!")
            await asyncio.sleep(60)
            if self.mc.state == "online":
                await self.mc.restart()
        except Exception:  # noqa: BLE001
            log.exception("auto restart")
        finally:
            self._restarting = False

    async def _scheduled_backup(self) -> None:
        self._backing_up = True
        try:
            path = await self.mc.backup()
            await self.send_console(f"💾 Scheduled backup created: `{path.name}`")
        except Exception as e:  # noqa: BLE001
            await self.send_console(f"❌ Scheduled backup failed: {e}")
        finally:
            self._backing_up = False

    async def close(self) -> None:
        try:
            await self.wake.close()
            await self.mc.shutdown()
        finally:
            await super().close()


def main(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    bot = PanelBot(cfg)
    try:
        bot.run(cfg["token"], log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("Invalid bot token. Run ./start.sh --setup to re-enter it.")
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Enable 'Message Content Intent' for your bot in the Discord Developer Portal → Bot.")
