# ⛏️ Minecraft Panel Bot

A Discord bot that replaces a hosting panel: start/stop button panel, remote console, plugin manager,
settings editor, backups, world upload, auto-shutdown – plus **DiscordSRV** for cross-chat.
Runs **Paper**; Java is downloaded automatically (right version for your Minecraft version).

## Install / update / reinstall

```bash
git clone https://github.com/<you>/minecraft-panel-bot.git
cd minecraft-panel-bot
chmod +x setup.sh start.sh kill.sh
./setup.sh
```

* **Fresh machine:** installs everything, runs the wizard, offers a systemd service.
* **Run it again later:** it detects the existing install and asks
  1. **Update** (`./setup.sh --update` skips the question) – keeps config, world, plugins and backups. It first **stops any running copy** of the bot/server, then pulls new panel code (`git pull`; if the folder came from a ZIP it offers to link it to your GitHub repo), refreshes dependencies, updates the Paper build and all plugins, installs missing core plugins (DiscordSRV), migrates old settings and restarts the service. The existing panel message is **edited, not re-posted**.
  2. **Clean reinstall** (`./setup.sh --clean`) – you must type `DELETE`. Removes config, server (world + plugins), Java and venv (and optionally backups), deletes the old panel message, then runs the wizard again.

Start manually with `./start.sh`; re-run only the wizard with `./start.sh --setup`.
Only one bot can run per folder (a lock prevents duplicates, which would otherwise each post a panel).

## ☠️ Kill / uninstall – leave the machine clean

```bash
./kill.sh
```

Stops the systemd service, the bot **and** the Minecraft server of this folder (gracefully, then force), deletes the panel message in Discord,
removes the service file, and deletes everything that was installed: `server/` (world, plugins), `runtime/` (Java), `.venv/`, `data/`, `config.json` (your token).
It asks (default **No**) before also deleting `backups/`, the source folder itself, and the system packages `setup.sh` installed
(only packages that were *not* there before – `setup.sh` records them in `.install_state`).

```bash
./kill.sh --yes             # no questions; keeps backups, source and system packages
./kill.sh --yes --purge     # no questions; ALSO deletes backups and this whole folder
./kill.sh --yes --packages  # also removes the system packages setup.sh installed
```

It only touches processes whose working directory is inside this folder, so other Python/Java programs on the machine are safe.

### Discord bot setup (once)
1. https://discord.com/developers/applications → New Application → **Bot** → copy token.
2. Bot tab → enable **Message Content Intent** and **Server Members Intent** (DiscordSRV needs both).
3. Invite with scopes `bot` + `applications.commands`; permissions: View Channels, Send Messages, Embed Links, Read Message History, Add Reactions, **Manage Webhooks** (player-head chat messages).
4. Create an OP role and 4 channels: `#panel`, `#cross-chat`, `#console`, `#configure` (hide console/configure from non-OPs).

### Wizard asks
Bot token · OP role ID · Panel / Cross-chat / Console / Configure channel IDs · EULA · RAM · version ·
plugins: **Install recommended** (Geyser, Floodgate, ViaVersion, ViaBackwards, ViaRewind, SkinsRestorer, spark) / **Custom** / **None**.
**DiscordSRV is always installed** and auto-configured (same token, cross-chat channel, webhook chat with player heads).

## Channels
| Channel | What it does |
|---|---|
| Panel | **One** message that is only ever edited (never re-posted; duplicates from older runs are removed at start). Status, player count, version, uptime (coarse: *Just now, 1 min, 2 mins, 1 hr…*). Updates only when something visibly changes. **Start** = everyone, **Stop / Restart** = OP role. Buttons react instantly – the panel switches to *Starting… / Stopping…* with a hint line, no extra chat messages. |
| Cross-chat | Handled by **DiscordSRV**: chat both ways, join/leave, advancements, deaths, start/stop messages. Customise in `server/plugins/DiscordSRV/`. |
| Console | Live server log. OP messages are run as console commands. |
| Configure | All slash commands (OP only, only usable here). |

## Slash commands (configure channel)
Type **`/help`** for an interactive guide (works anywhere). If commands don't show up: press **Ctrl+R**, or mention the bot with `@bot sync`
(`@bot invite` prints the invite link). The invite link is also printed in the log at every start and by the wizard –
the bot **must** be invited with the `applications.commands` scope. Make sure the bot's role can **view** all four channels, and has **Read Message History** in the panel channel (so it can always find its one panel message).

`/server` start·stop·restart·kill · `/autoshut` · `/autostart` (wake the server when someone tries to join) · `/autorestart` · `/plugin` (search/install/remove/update all) · `/plugin_upload` ·
`/settings` (server properties + game rules) · `/motd` (server-list text, `&a` colours, `\n` 2nd line) · `/icon` (server-list picture, auto 64×64) ·
`/ram` · `/version` (change Minecraft version, auto-backup) · `/world upload` (replace world from .zip attachment or direct link) · `/world reset` ·
`/backup create|list|restore|delete|schedule` · `/players` · `/whitelist` · `/op` `/deop` `/kick` `/ban` `/pardon` · `/command`

## Auto-start on join
While the server is offline the bot listens on the Minecraft port (25565). The server list shows "Offline – join to start the server!";
an actual join attempt shows "starting, reconnect in a minute" and boots the server. Pings never start it, and if the whitelist is on only
whitelisted names can. Toggle with `/autostart`. Java Edition only (Bedrock/Geyser players can use the panel's Start button).
Combined with `/autoshut` the server sleeps when empty and wakes when someone knocks.

## Notes
- Open **TCP 25565** (Java) and **UDP 19132** (Bedrock/Geyser).
- `config.json` holds your token and is git-ignored – never commit it. `server/`, `backups/`, `runtime/`, `data/` are ignored too.
- MOTD / icon / most properties apply after a restart. Game rules apply live and on every start.
- `/world upload` accepts a zip containing the world folder (the folder with `level.dat`, zipped alone or inside a folder). Bukkit-style `_nether` / `_the_end` folders are picked up too.
- Bedrock whitelist: `/whitelist add player:.Name`. Windows: use WSL2.
