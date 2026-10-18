# ⛏️ Minecraft Panel Bot

A Discord bot that replaces a hosting panel: start/stop button panel, remote console, plugin manager,
settings editor, backups, world upload, auto-shutdown – plus **DiscordSRV** for cross-chat.
Runs **Paper**; Java is downloaded automatically (right version for your Minecraft version).

## Install / update / reinstall

```bash
git clone https://github.com/<you>/minecraft-panel-bot.git
cd minecraft-panel-bot
chmod +x *.sh
./setup.sh
```

**Fresh machine:** installs everything, runs the wizard, then asks how to start the bot (terminal / background / not now). **systemd is never touched unless you ask for it** (see below).

### Updating (your data is never touched)
```bash
git pull   # or just:
./setup.sh --update
```
Updates **only the bot code, scripts and Python dependencies**. Your `config.json`, `server/` (world, plugins, server.properties), `data/`, `backups/` and the downloaded Java stay **byte-for-byte identical** – they are all git-ignored, so `git pull` cannot overwrite them.
It stops any old copy of the bot first (a leftover copy is what causes duplicate panels), makes a safety copy `config.json.bak`, and the existing panel message is edited, not re-posted.
Want the newest Paper build and plugins too? That is a separate, optional step because it changes files inside `server/`:
`./setup.sh --update --with-server` (or answer *y* when the menu asks). Plugins can also be updated from Discord with `/plugin → Update all`, the Minecraft version with `/version`.

Running `./setup.sh` on an existing install shows a menu: **1) Update** · **2) Clean reinstall** (type `DELETE`; wipes everything and starts over) · **3) Cancel**.

### Running it: terminal, background, or systemd (optional)
| | |
|---|---|
| `./start.sh` | run in this terminal (Ctrl+C stops it) |
| `./start.sh --bg` | run in the **background**, survives closing the terminal. Logs: `tail -f logs/bot.log` |
| `./stop.sh` | stop the bot **and** the Minecraft server safely (world is saved first) |
| `./setup.sh --service` | *optional:* install a systemd service (auto-start on boot). Never done automatically |
| `./setup.sh --remove-service` | remove that service again |

Only one bot can run per folder (a lock prevents duplicates). Re-run only the wizard with `./start.sh --setup`.

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

## Auto-shutdown (`/autoshut`)
Stops the server only after **no players for the set time (default 3 min)**. It never trusts the console log alone: besides the join/leave lines it asks the server itself (status ping + `list`), and **it will not stop unless the server confirms zero players** – if it can't verify, it keeps the server running and re-checks.

## Auto-start on join (Java **and** Bedrock)
While the server is offline the bot listens on the Minecraft ports (TCP 25565, and UDP 19132 when Geyser is installed).
* **Java:** the server list shows your MOTD + "Offline – join to start the server!". A real join attempt shows "starting, reconnect in a minute" and boots the server. With the whitelist on, only whitelisted names can wake it.
* **Bedrock:** the server appears in the Bedrock server list as offline; tapping join starts the server. Retry after about a minute. (After the server has been online once, the bot remembers Geyser's real version info so the offline entry matches exactly.)
* Pings never start the server. Toggle with `/autostart`. Combined with `/autoshut` the server sleeps when empty and wakes when someone knocks.

## Notes
- Open **TCP 25565** (Java) and **UDP 19132** (Bedrock/Geyser) on your firewall/host.
- `config.json` holds your token and is git-ignored – never commit it. `server/`, `backups/`, `runtime/`, `data/` are ignored too.
- MOTD / icon / most properties apply after a restart. Game rules apply live and on every start.
- `/world upload` accepts a zip containing the world folder (the folder with `level.dat`, zipped alone or inside a folder). Bukkit-style `_nether` / `_the_end` folders are picked up too.
- Bedrock whitelist: `/whitelist add player:.Name`. Windows: use WSL2.
- `./kill.sh` removes everything (see above), including `logs/`.
