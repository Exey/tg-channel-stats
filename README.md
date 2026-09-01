# TG Channel Stats

A cross-platform desktop app for analyzing Telegram channels. Point it at any
public channel (or a private one you're a member of), pick a period, and it
scans every post to rank the top performers **and** compute activity
analytics. Track many channels side by side, organize them into folders and
tags, and use the folder-level views to study cross-promotion, content
quality, and ad-swap ("mutual PR") potential across a whole group of
channels — all on dashboards you can re-open, re-fetch, and export.

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
produces one JSON checkpoint holding:

- **Engagement ranking** — per-post views, reactions, forwards ("private
  reposts"), comments, and media type, with albums merged into one row. It
  keeps the union of the top-N by each metric *plus* the most recent top-N
  *plus* the best post of every calendar month, so the on-screen table can
  re-sort by any column and still show the true leaders, and the
  quality/recent views never silently drop a month. Optionally fetches
  **public reposts** for that pool (which channels re-shared each post),
  where stats access allows.
- **Activity analytics** — member count, creation date, posts/day,
  average/max views, average/max reposts, average reactions, share of posts
  with media, "settled" average views (posts older than 14 days, whose view
  count has stopped climbing), a trimmed repost average, viral-post share,
  and distributions by **hour of day**, **day of week**, and **month** (with
  per-month view/share/reaction totals over *every* scanned post, not just
  the top-N sample).

Each analyzed channel is saved as its own JSON checkpoint and listed in the
sidebar, so closing the app (or a crash mid-scan) never loses a fetch and you
can re-open any channel instantly without re-scanning Telegram.

## Features

### Per-channel dashboard

Stat cards (members, posts, posts/day, avg views, **ERR%**, **ERV%**,
**Virality index**, avg reposts/reactions — see
[Stats & scoring algorithms](#stats--scoring-algorithms)), a wide activity
trend chart with toggleable Views / Reactions / Shares / Posts / **Quality**
lines (month or season buckets), by-hour and by-weekday bar charts, a "Last
50 Posts" card row with per-post content-quality gauges, and a sortable
top-posts table. Click a post to open it in Telegram; click a reposts cell
to see who re-shared it. Export the report as Markdown or copy a plain-text
link list.

### Multi-channel comparison

- **Metrics** (`⚖️⭐️`) — pick 2–8 channels from the sidebar and see their
  stat cards laid out one column per channel for an easy diff, exportable as
  a Markdown table.
- **Charts** (`⚖️📈`) — pick up to 8 channels and overlay them on five
  stacked trend charts (Quality, Views, Shares, Reactions, Posts), sharing
  one month/season toggle.

### Folders & Tags view (`📁`)

The **Folders** and **Tags** cards sit at the top:

- **Folders** — user-defined colored groups, managed in-app. Assign channels
  from the sidebar's right-click menu, the dashboard's folder button, or the
  "assign every channel to a folder" bulk action here. The sidebar can group
  and sort by folder. Also here: a **Markdown export** (one row per channel,
  optionally with per-period Rating / Views / Viral share) and **Refresh
  comments** (re-read just the comment count for a folder's channels).
- **Tags** — a lightweight one-tag-per-channel taxonomy loaded from a
  Markdown table (`| tag | long tag | description |`); edit the source `.md`
  and reload to change the tag set. Only the per-channel assignment is
  app-owned. A shared tag is the niche signal behind MPR Pairs (folders
  count for much less there).

Below that, for one folder and one period (monthly / seasonal / half-year /
rolling-year window): **periodic stats** — per-channel views / shares /
reactions / viral-share, the period's most-viewed post, **Post Quality**,
and a composite **Rating** (see [rating.py](#composite-channel-rating)),
exportable as Markdown. (The cross-channel reposts table that used to live
here now sits at the bottom of the Mutual PR view.)

### High-Quality Posts view (`🎯`)

For one folder and period, a grid of individual **posts** (not channels)
ranked by content quality — proportional engagement relative to that post's
own reach, not raw view count (see
[scoring.py](#per-post-content-quality)). Filters for minimum
followers, hiding text-only posts, and per-channel caps. Optional on-demand
**thumbnail fetch** (downloads only the smallest preview image for the posts
on screen into a local cache — nothing else in the app downloads media).
Export as a Markdown table or a copyable Tg-links list with a "top authors"
summary.

### Mutual PR (ad-swap) view (`🤝`)

Every tracked channel in one sortable table: followers, an estimated
**ad-post follower-gain forecast** at five horizons (24h / 48h / 72h / week /
month), a "repeated after a month" estimate for a reminder post, and the
least-crowded weekdays to slot an ad in (each with a "how much better than an
average day" rate). Meant to help decide which channels are worth trading ad
posts with. All figures beyond Followers are heuristic estimates — see
[scoring_pr.py](#mutual-pr-ad-swap-forecast) — and the view
says so in the UI.

Below the table sit two more cards: **MPR Pairs** — channel pairs ranked for
an ad swap by size / engagement / timing / niche compatibility, with a
**Best posting days** column showing each side's best days plus `★` for the
days that suit both at once (see
[scoring_pr.py](#mutual-pr-partner-matching)) — and, at the bottom, the
**cross-channel reposts** table (who already reposts whom — moved here from
the Folders & Tags view). The **Markdown export** keeps the main forecast table intact
and appends just the MPR Pairs table (`## Пары ВП`).

### Login, profiles, connection settings

- **Two login flows** — QR code (scan from Telegram → Settings → Devices) or
  the classic phone number + SMS code, with 2FA-password support. The session
  is saved, so later fetches need no re-login.
- **Named profiles** — keep several accounts/API keys side by side.
- **`.env` import/export** of connection settings (also understands
  `TG_API_ID` / `TG_API_HASH` / `TG_PHONE` naming).
- **Refresh comments** — a folder-wide action (on the Folders & Tags view)
  that re-reads just the comment count for stored posts (added after some
  checkpoints were fetched) without a full re-scan.
- **Lean refresh** — the dashboard's **Refresh** button, and a Config-screen
  card with a staleness list of every tracked channel (oldest fetch first,
  with age, name and followers) plus one-click batch buttons: **Oldest 10**,
  **1 mo+**, **3 mo+**. It's *incremental* — only the months since a channel
  was last fetched are re-scanned and merged into the existing checkpoint,
  so a monthly cadence re-reads ~1 month instead of the whole stored 2–3
  year period. (A checkpoint too old to merge falls back to one full scan.)

### UI

- **Bilingual** — English and Russian, switchable at runtime with no widget
  rebuild (a running fetch and unsaved fields survive the switch).
- **Light / Dark theme** — follows the OS appearance by default (and updates
  live if you flip it), or pin System / Light / Dark from the **Theme** menu
  or the picker on the Config screen.
- Telegram work runs on a background thread, so the GUI never freezes; scans
  are cancellable. Rate limits (FloodWait) and transient network errors are
  retried automatically with backoff.
- Charts are drawn natively with QPainter — no matplotlib or QtCharts.

## Stats & scoring algorithms

Three modules hold the formulas, kept separate from the views so multiple
views can share one implementation. Each module's own docstring carries the
full rationale and the tuning history; this is the summary.

### Channel-level metrics

Computed once per fetch by `app/tools/channel_stat.py` and stored in the
checkpoint's `stats`:

| Metric | Formula | Notes |
| --- | --- | --- |
| **ERR%** (engagement rate by reach) | `avg_views_settled / members × 100` | `avg_views_settled` averages only posts older than 14 days, whose view count has settled. |
| **ERV%** (engagement rate by views) | `(avg_reactions + avg_reposts) / avg_views × 100` | Shown on the dashboard and Compare views. |
| **Virality index** | `max_views / avg_views` | Spread between the best post and the average — how often the channel lands an outlier hit. |
| **Viral post share** | `% of posts with views > 2 × avg_views` | Feeds the Rating and Mutual PR math. |
| **Avg reposts (trimmed)** | mean after dropping the top 10% of posts by repost count | Reposts are far more top-heavy than views/reactions, so the plain average swings on one-off spikes. |

### Per-post content quality

`app/scoring.py` ranks individual posts by *proportional* engagement — how much a post
punched above its own reach — not by raw views. Shared by the High-Quality
Posts grid, the dashboard's Quality trend line and post cards, and the
Folders & Tags "Post Quality" column; it also feeds the Rating's quality term
and Mutual PR's "Interest".

Per post, given the post's `views`, `reactions`, `forwards`, `comments`, and
its channel's `avg_views`:

1. A post with an **inline keyboard** (`has_buttons`) scores **0** outright —
   an ad/CTA button is what's driving its engagement, not the content.
2. `comments` are capped at 100 (past that a post clearly has real
   discussion either way).
3. `reaction_wt` is tapered in two brackets: the first 1 000 reactions count
   at 0.045 each, 1 000–10 000 at only 0.005 each, beyond 10 000 nothing more
   is added (reaction counts can run anomalously high relative to a post's
   own views).
4. `viral_excess = max(0, views − avg_views)` — a bonus for beating the
   channel's *own* usual reach, weighted 0.2.
5. `ERV% = (forwards×1.0 + comments×0.25 + reaction_wt + viral_excess×0.2) / views × 100`
6. `raw = ERV% × 100`
7. `gauge = 1000 × raw / (raw + 580)` — a saturating curve onto the 0–1000
   gauge (K = 580 ≈ this app's real per-post median raw score, so a typical
   post lands mid-gauge; a hard clamp would flatten most real posts at the
   ceiling).

Numerator terms are ordered by how much each really signals quality:
forwards (a deliberate, costly share) first, then comments, then reactions
(cheapest, easiest to pump).

### Composite channel Rating

`app/rating.py` produces a per-channel-per-period score in `[0, 1]`, shown in the Folders & Tags view's
"Rating" column and reused verbatim by that view's Folders MD export. Channels
are grouped by folder and each channel is normalized against **only its own
folder's peers** for the same period.

For each channel entry in a period bucket (`views`, `shares`, `reactions`,
`quality`, `viral_share`):

- **views** — min-max normalized within the bucket, weight `0.70` (the
  single biggest weight).
- **engagement** — `shares + 0.05 × reactions`, min-max normalized, weight
  `0.65`. Reactions count for only 5% of raw value because reaction counts
  are the easier of the two to artificially pump; a repost is a costlier,
  harder-to-fake action.
- **quality** — the per-post gauge above, averaged per channel per period,
  min-max normalized, weight `0.45`.
- **virality** — an *absolute* (not normalized) curve over `viral_share`:
  0% → 0, 1% → 0.05, ramping to 1.0 at 15%+. Its weight itself ramps from
  `0.10` to `0.51` as `viral_share` climbs to 15%, so virality matters more
  to a channel that actually is viral.

`score = (weighted sum of the four terms) / (sum of the four actual
weights)` — that division is the one and only normalization step.

**Zero-reposts red flag:** a channel with ~0 reposts in the period has its
`viral_share` cut by 40% *before* the virality curve, and the whole
composite score cut by another 40% flat — apparent virality nobody actually
shared is a warning sign the other metrics don't catch.

### Mutual PR ad-swap forecast

Unlike `app/scoring.py` (tuned against this app's own real median),
`app/scoring_pr.py` is built on **documented heuristics, not measurements** — there is no real
ad-campaign outcome data anywhere in the app. The constants exist so a
forecast can be produced at all, and are ordinary module constants
specifically so they can be retuned once real ad-swap outcomes are logged.

- **`AD_VIEW_CURVE`** — assumed fraction of a post's eventual reach landed by
  each horizon: 24h `0.49`, 48h `0.75`, 72h `0.78`, week `0.80`, month
  `1.00`.
- **Reach basis** — `avg_views_settled`, capped at `followers × 1.0`. Views
  beyond a channel's own subscriber base reflect viral/external discovery a
  freshly-placed ad post wouldn't inherit.
- **Follow-conversion rate** —
  `0.036 × (0.5 + weight × interest_gauge/1000)`, where `weight = 0.3 ×
  size_band_factor(followers)`. Content quality ("Interest", the average
  post-quality gauge over the last ~90 days) swings the rate only ~0.5–0.8×;
  that swing is tripled for channels in the 5 000–30 000 follower band,
  where quality reads as a more reliable predictor.
- **Viral boost** — a ≥1 multiplier from `viral_post_share`, applied only
  within the 5K–30K band and only when the reach cap above didn't already
  fire (so virality isn't credited twice).
- **`size_forecast_multiplier`** — a deliberate flat scale-up on top of the
  calibrated base: **×3.0 for channels up to ~6 000 followers, ×1.5 for
  larger** (linear between 6 000 and 7 000), since smaller channels convert
  a borrowed audience into followers far better.
- **`total = reach × rate × boost × size_forecast_multiplier`**, then
  **redistributed** across the
  horizon fractions by posting rarity: a channel posting below the app's
  median (~0.525 posts/day) has its post linger un-buried, so its reach
  arrives more gradually — early-horizon fractions shrink toward later ones
  (month's fraction is always exactly 1.0, so no horizon can ever exceed the
  total). The rarity effect is squared to separate moderate from extreme
  rare-posters and scaled down for channels with fewer than 20 stored posts.
- **`link_behavior_factor`** — ×(up to 1.05) for a channel that consistently
  single-mentions other channels (genuine cross-promotion), ×(down to 0.95)
  for one that consistently posts external-link spam.
- **`ad_forecast_range`** — a crude ±band (low `×0.40`, high `×1.80`)
  combining the two dominant unverified constants; honestly labeled, not a
  fitted interval.
- **`repeated_post_forecast`** — a reminder post a month later, decayed by
  how many of the channel's own posts ran in between:
  `retention = 1 / (1 + posts_between / 16)`.
- **`best_days`** — least-crowded weekdays scored
  `(1 − normalized post rate) × interest_gauge`; the displayed "better than
  an average day" rate is damped to 25% of its raw deviation (the ranking
  still uses the undamped score).

### Mutual PR partner matching

`rank_mutual_pr_pairs` (bottom of `app/scoring_pr.py`) scores every *pair*
of channels for how good an ad swap between them would be — the basis for
the **MPR Pairs** card and the `## Пары ВП` section appended to the Mutual
PR Markdown export. It uses only metrics the app already has (no mention
graph, so it can't tell whether two channels have already promoted each
other — filter those out upstream). The four components are a plain weighted
sum in `[0, 1]`:

| Component | Weight | Formula |
| --- | --- | --- |
| **`size_parity`** | 0.30 | `1 − abs(log10 subs_A − log10 subs_B) / log10(100)`, clamped to `[0, 1]` — 1.0 for equal size, 0 once one channel is 100× the other. Log-scaled, so a 2× gap scores the same at any absolute size. |
| **`quality_parity`** | 0.30 | `1 − abs(f24_A − f24_B) / (f24_A + f24_B)` — how close the two 24h ad-post forecasts are (a proxy for "both convert ad views similarly"). |
| **`day_overlap`** | 0.20 | shared entries in the two channels' top-2 `best_days` over `min(len_A, len_B)` — 1.0 when both top-2 sets match. |
| **`niche_affinity`** | 0.20 | `1.0` if the two channels carry the **same tag** (a real niche match), `0.30` (`MUTUAL_PR_FOLDER_NICHE`) if they only share a **folder**, else `0`. Tag-first on purpose: a folder is just sidebar organization, so a different-folder same-tag pair beats a same-folder unrelated-tag one. |

The MPR Pairs table (UI card and Markdown export) lists **every pair scoring
`MUTUAL_PR_MIN_SCORE` (0.51) or higher, best first, capped at
`MUTUAL_PR_MAX_PAIRS` (500)** — the low floor just keeps the ranked tail
available; the cap is what bounds the table.
Its **Best posting days** column shows each channel's own best days (`A: … ·
B: …`) and prefixes `★` for the days that are a good ad slot in *both*
channels at once — `mutual_best_days`, which takes any weekday above each
channel's own average (not just a strict overlap of their top-2, which
would miss a day ranked #3 for one side but still clearly above its
average). All weights and constants are ordinary module-level values, meant
to be retuned.

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
   dashboard; **Refresh** for a lean (incremental) update, **Export** to
   save a Markdown report, **Remove** to drop it.
4. Open **Folders & Tags** from the sidebar to group channels, then use the
   **High-Quality Posts** or **Mutual PR** views for folder-wide analysis.

**Which channels can I analyze?** Any public channel by `@username`, or a
private one you're a member of by its `-100…` ID or `t.me` link.

## Where data is stored

Config, sessions, checkpoints, caches and logs live in the OS-standard
per-user locations under an app folder (`TgChannelStat`). Open **File → Open
config folder** to jump straight there.

| Data | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Config, sessions, checkpoints, folders, tags, media cache | `~/Library/Application Support/TgChannelStat` | `%APPDATA%\TgChannelStat` | `$XDG_CONFIG_HOME` or `~/.config/TgChannelStat` |
| Logs | `~/Library/Logs/TgChannelStat` | `%LOCALAPPDATA%\TgChannelStat\logs` | `$XDG_STATE_HOME` or `~/.local/state/tgchannelstat` |

- **`config.json`** — language, theme, profiles, and connection fields.
- **Sessions** (`sessions/`) — Telethon session files (one per profile).
- **Checkpoints** (`checkpoints/<channel>.json`) — the per-channel fetch
  results shown in the sidebar. `@Name`, `Name`, and the `-100…` ID all map
  to the same checkpoint.
- **`folders.json`** — folder definitions and channel→folder assignments.
- **`tags.json`** — the loaded tag list, its source `.md` path, and
  channel→tag assignments.
- **`media/`** — cached post thumbnails downloaded on demand by the
  High-Quality Posts / dashboard "Fetch media" button.
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
├── folders.py              # folder definitions + channel assignments
├── tags.py                 # tag taxonomy loaded from a Markdown table
├── periods.py              # month/season/rolling-year period-key helpers
├── media_cache.py          # on-disk thumbnail cache paths
├── scoring.py              # per-post content-quality formula (shared)
├── rating.py               # composite per-channel-per-period Rating (shared)
├── scoring_pr.py           # Mutual PR ad-swap forecast heuristics
├── worker.py               # QThread workers: login flows + tool runs
├── i18n.py                 # English / Russian strings
├── text_utils.py           # small string helpers
├── errors.py               # friendly OS-error messages
├── tools/
│   ├── channel_stat.py     # the scan: engagement ranking + activity stats
│   ├── comments_refresh.py # re-read just the comment count for stored posts
│   ├── lean_refresh.py     # incremental re-scan of the months since last fetch
│   ├── media_fetch.py      # on-demand post-thumbnail download
│   └── common.py           # entity resolution, FloodWait retries
└── ui/
    ├── main_window.py         # sidebar + stacked content views
    ├── config_view.py         # credentials, profiles, login, fetch form, folder MD export
    ├── dashboard_view.py      # stat cards, charts, post cards, top-posts table, export
    ├── compare_view.py        # side-by-side stat cards for 2-8 channels
    ├── compare_charts_view.py  # overlaid trend charts for up to 8 channels
    ├── folder_stat_view.py    # Folders & Tags: hosts the folder/tag cards + periodic stats + Rating
    ├── content_quality_view.py # High-Quality Posts grid
    ├── mutual_pr_view.py      # ad-swap follower-gain forecast table
    ├── folder_dialog.py       # folder manager dialog
    ├── side_panel.py          # Config + fetched-channels list, compare modes
    ├── charts.py              # native QPainter chart widgets
    ├── qr_login_dialog.py     # QR-code login dialog
    ├── widgets.py             # shared card widgets (StatCard, PostCard, gauges…)
    └── theme.py               # QSS stylesheet + palette
assets/svgs/                # UI icons
```

## Notes & limitations

- **Folder-level views read stored checkpoints, not fresh Telegram data.**
  Per-period view/share/reaction totals come from every scanned post and are
  accurate; the reposts-between-channels table and per-post quality rely on
  the stored top-N sample, so they're only as complete as the top-N and
  "include public reposts" choices made when each channel was fetched.
- **New per-post fields** (`comments`, `media_type`, `has_buttons`) are only
  present on checkpoints fetched after they were added — older checkpoints
  show 0 comments, a text-only placeholder icon, and no ad-button exclusion
  until refetched (or, for comments, until "Refresh comments" is run).
- **Mutual PR forecasts are heuristics, not measurements** — see
  [scoring_pr.py](#mutual-pr-ad-swap-forecast). Treat the
  numbers as rough order-of-magnitude guidance.
- **Private channels typed as bare numeric IDs**: Telethon can only resolve a
  peer it has an `access_hash` for. If a `-100…` ID isn't found, the app falls
  back to scanning your dialogs — so being a member of the channel is what
  makes it resolvable.
- **Public reposts** require a channel you have statistics access to; where
  unavailable, that column is simply left blank.
- This tool only reads data your own account can already see; it does nothing
  a normal Telegram client couldn't.
