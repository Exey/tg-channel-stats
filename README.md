# TG Channel Stats

A cross-platform desktop app for analyzing Telegram channels. Point it at any
public channel (or a private one you're a member of), pick a period, and it
scans every post to rank the top performers **and** compute activity
analytics — then presents both on a dashboard you can re-open, re-fetch, and
export.

Built with [PySide6](https://doc.qt.io/qtforpython/) (Qt for Python) and
[Telethon](https://docs.telethon.dev/). English and Russian UI.

## Run from source

The quickest way — the script creates a `.venv` on first run, installs
dependencies, and launches the app:

```bash
./run_dev.sh          # macOS / Linux
run_dev.bat           # Windows
```

Re-run the same script any time to start the app; it only reinstalls if
`requirements.txt` changed. See [Installation](#installation) for the manual
equivalent.

![tgchanstat.png](https://i.postimg.cc/MWnhLs56/tgchanstat.png)

## What it does

For a chosen channel and time window, a single pass over the channel's history
produces:

- **Engagement ranking** — per-post views, reactions and forwards ("private
  reposts"), with albums merged into one row. It keeps the union of the top-N
  by each metric, so the on-screen table can re-sort by any column and still
  show the true leaders. Optionally fetches **public reposts** for that pool
  (which channels re-shared each post), where stats access allows.
- **Activity analytics** — member count, creation date, posts/day,
  average/max views, average reactions, share of posts with media, and
  distributions by **hour of day**, **day of week**, and **month**.

Each analyzed channel is saved as its own JSON checkpoint and listed in the
sidebar, so closing the app (or a crash mid-scan) never loses a fetch and you
can re-open any channel instantly without re-scanning Telegram.

## Features

- **Dashboard** with stat cards, activity charts (monthly / hourly / weekday),
  and a sortable table of top posts. Click a post to open it in Telegram;
  click a reposts cell to see who re-shared it.
- **Two login flows** — QR code (scan from Telegram → Settings → Devices) or
  the classic phone number + SMS code, with 2FA-password support. The session
  is saved, so later fetches need no re-login.
- **Named profiles** — keep several accounts/API keys side by side.
- **`.env` import/export** of connection settings (also understands
  `TG_API_ID` / `TG_API_HASH` / `TG_PHONE` naming).
- **Markdown export** of a channel report (stats + top-posts table).
- **Bilingual UI** — English and Russian, switchable at runtime.
- **Light / Dark theme** — follows the OS appearance by default (and updates
  live if you flip it), or pin Light/Dark explicitly from the **Theme** menu.
- Runs Telegram work on a background thread, so the GUI never freezes; scans
  are cancellable.

## Requirements

- Python 3.12+ (developed on 3.12)
- Telegram API credentials — an **API_ID** and **API_HASH** from
  [my.telegram.org](https://my.telegram.org)

## Installation

Prefer [`run_dev.sh` / `run_dev.bat`](#run-from-source) above — this is the
manual equivalent if you'd rather manage the environment yourself:

```bash
# from the project root
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 main.py                  # Windows: python main.py
```

Dependencies (`requirements.txt`):

- `PySide6>=6.6` — Qt GUI
- `telethon>=1.34` — Telegram client
- `qrcode[pil]>=7.4` — renders the QR-code login image

## Building standalone binaries

`build.sh` / `build.bat` package the app into a standalone binary (no Python
install required to run it) via [PyInstaller](https://pyinstaller.org/):

```bash
./build.sh          # macOS / Linux
build.bat           # Windows
```

PyInstaller doesn't cross-compile, so build on each OS you want a binary
for. Output goes to `dist/` as `tg-channel-stats_<version>` (dots as
underscores, e.g. `tg-channel-stats_26_7_30`):

- **macOS** — `tg-channel-stats_26_7_30.app`
- **Windows** — `tg-channel-stats_26_7_30.exe`
- **Linux** — `tg-channel-stats_26_7_30`

The version string comes from `app/version.py`. Windows and Linux binaries
also build automatically in CI on every push to `main`
(`.github/workflows/build.yml`) — download them from the workflow run's
Artifacts; macOS is left out of CI (10x the Actions minutes of Linux) and
built locally instead.

## Getting API credentials

1. Open [my.telegram.org](https://my.telegram.org) and log in with your phone
   number (Telegram sends the code to your Telegram app).
2. Click **API development tools**.
3. Fill in *App title* and *Short name* (any text), platform *Desktop*, then
   create the application.
4. Copy **App api_id** → `API_ID` and **App api_hash** → `API_HASH`.
5. `PHONE_NUMBER` must be in international format, e.g. `+79001234567`.

## Usage

Launch with [`run_dev.sh` / `run_dev.bat`](#run-from-source) (or `python3
main.py` in an activated venv), then:

1. On the **Config** screen, enter your `API_ID`, `API_HASH` and
   `PHONE_NUMBER` (or import a `.env`), then authorize — QR code is the quick
   path.
2. In **Fetch a channel**, enter a channel by `@username`, `t.me` link, or
   `-100…` ID, choose how many top posts to keep per metric and the period of
   analysis, and (optionally) enable public-repost lookup.
3. Click **Fetch & analyze**. The channel appears in the sidebar with its
   dashboard; **Re-fetch** to refresh it, **Export** to save a Markdown
   report, **Remove** to drop it.

**Which channels can I analyze?** Any public channel by `@username`, or a
private one you're a member of by its `-100…` ID or `t.me` link.

## Where data is stored

Config, sessions, checkpoints and logs live in the OS-standard per-user
locations under an app folder (`TgChannelStat`). Open **File → Open config
folder** to jump straight there.

| Data | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Config, sessions, checkpoints | `~/Library/Application Support/TgChannelStat` | `%APPDATA%\TgChannelStat` | `$XDG_CONFIG_HOME` or `~/.config/TgChannelStat` |
| Logs | `~/Library/Logs/TgChannelStat` | `%LOCALAPPDATA%\TgChannelStat\logs` | `$XDG_STATE_HOME` or `~/.local/state/tgchannelstat` |

- **Config** (`config.json`) holds language, profiles, and connection fields.
- **Sessions** are Telethon session files (one per profile) — your login.
- **Checkpoints** (`checkpoints/<channel>.json`) are the per-channel fetch
  results shown in the sidebar. `@Name`, `Name`, and the `-100…` ID all map to
  the same checkpoint.
- **Logs** rotate at ~2 MB, keeping 5 backups.

## Project layout

```
main.py                     # entry point: logging, theme, main window
run_dev.sh / run_dev.bat    # create venv, install deps, launch (macOS·Linux / Windows)
build.py                    # PyInstaller packaging, invoked by build.sh/build.bat
build.sh / build.bat        # create venv, install build deps, package a binary
requirements.txt
requirements-build.txt      # PyInstaller, only needed to package binaries
app/
├── version.py              # app version string (CalVer: YY.M.D)
├── config.py               # JSON config: profiles + .env import/export
├── store.py                # per-channel JSON checkpoint store
├── worker.py               # QThread workers: login flows + tool runs
├── i18n.py                 # English / Russian strings
├── tools/
│   ├── channel_stat.py     # the scan: engagement ranking + activity stats
│   └── common.py           # entity resolution, FloodWait retries
└── ui/
    ├── main_window.py      # sidebar + stacked Config / Dashboard views
    ├── config_view.py      # credentials, profiles, login, fetch form
    ├── dashboard_view.py   # stat cards, charts, top-posts table, export
    ├── side_panel.py       # Config + fetched-channels list
    ├── charts.py           # activity chart widgets
    ├── qr_login_dialog.py  # QR-code login dialog
    ├── widgets.py          # shared widgets
    └── theme.py            # QSS stylesheet
assets/svgs/                # UI icons
```

## Notes & limitations

- **Private channels typed as bare numeric IDs**: Telethon can only resolve a
  peer it has an `access_hash` for. If a `-100…` ID isn't found, the app falls
  back to scanning your dialogs — so being a member of the channel is what
  makes it resolvable.
- **Public reposts** require a channel you have statistics access to; where
  unavailable, that column is simply left blank.
- Rate limits (FloodWait) and transient network errors are retried
  automatically with backoff.
- This tool only reads data your own account can already see; it does nothing
  a normal Telegram client couldn't.
