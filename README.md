# etekcity-bp-daemon

A standalone Linux daemon that connects to an Etekcity Smart Blood Pressure
Monitor over Bluetooth Low Energy (BLE) and logs its readings to a local
SQLite database. No cloud account, no companion app, no Home Assistant
required.

It's a thin wrapper around the
[`etekcity-bp-ble`](https://github.com/bonelifer/etekcity-bp-ble) library,
packaged to run unattended as a `systemd` service on something like a
Raspberry Pi sitting near the monitor.

**Disclaimer: This is an unofficial, community-developed project. It is not
affiliated with, officially maintained by, or in any way officially
connected with Etekcity Corporation or Guangdong Transtek Medical
Electronics Co., Ltd. Nothing here is medical advice; the AHA category
labels and crisis-range alerting are informational only. Talk to a
doctor about your blood pressure readings.**

## Supported device

Only the TMB-1583-BS has been tested, but other models using the same BLE
protocol may work.

## Features

- Scans for the device on first run, then pins its BLE address into the
  config file so future restarts connect directly instead of re-scanning
- Records every reading (systolic, diastolic, pulse, irregular-heartbeat
  and motion flags) to a local SQLite database
- Runs as a `systemd` service with automatic restart on failure
- Optional PDF/CSV reports shaded by AHA blood-pressure category, with a
  systolic/diastolic trend chart and a choice of table layout (full,
  compact, or a weekly/monthly rollup for long histories)
- Optional Apprise-based alerting on stale data, hypertensive-crisis-range
  readings, or an irregular heartbeat
- Optional read-only HTTP API and MQTT publishing
- Optional "who was this?" profile tagging for a device shared by more than
  two people, via ntfy or dunstify, not limited to the device's own
  two-slot user distinction
- Optional per-profile report personalization (name/email/notes, preferred
  unit/date format/page size, and doctor-set blood-pressure/pulse goals
  with trend tracking)
- Optional per-profile alert routing and threshold overrides, so different
  people's alerts can go to different places

## Installation

Requires Python 3.11+.

### Quick install

```bash
git clone https://github.com/bonelifer/etekcity-bp-daemon.git
cd etekcity-bp-daemon
sudo ./install.sh
```

This creates a venv at `/opt/etekcity-bp-daemon`, installs the package from
the checkout, seeds `/etc/etekcity-bp-daemon/config.ini` (if it doesn't
already exist), creates an `etekcity-bp-daemon` system user, and installs
and enables the systemd service. It also installs (but does not enable) the
[scheduled report generation](#scheduled-report-generation) and
[alerting](#alerting) timer units, and the [HTTP API](#http-api) service.
It's safe to re-run: it skips steps that are already done. Edit the config
and `sudo systemctl restart etekcity-bp-daemon` afterward.

`config.ini` can hold real secrets (ntfy/API tokens, `apprise_urls` with
embedded credentials), so `install.sh` sets it to mode `600`, owned by the
`etekcity-bp-daemon` user, every time it runs (including on re-runs, in case
it was ever loosened). Running the CLI tools by hand afterward needs
`sudo -u etekcity-bp-daemon`, e.g.:

```bash
sudo -u etekcity-bp-daemon etekcity-bp-report --config /etc/etekcity-bp-daemon/config.ini
```

### Manual install

```bash
python3 -m venv /opt/etekcity-bp-daemon/venv
/opt/etekcity-bp-daemon/venv/bin/pip install /path/to/etekcity-bp-daemon  # this checkout
```

#### Config file

```bash
sudo mkdir -p /etc/etekcity-bp-daemon
sudo cp config/etekcity-bp-daemon.ini.example /etc/etekcity-bp-daemon/config.ini
sudo "$EDITOR" /etc/etekcity-bp-daemon/config.ini
```

Leave `[monitor] address` empty to auto-discover the device on first run
(power it on with the `MEM` button while the daemon is scanning). Once
found, the daemon writes the address back into this file so it reconnects
directly on every future start. See
[config/etekcity-bp-daemon.ini.example](config/etekcity-bp-daemon.ini.example)
for every setting, with inline documentation.

Validate a config file without starting the daemon:

```bash
etekcity-bp-daemon --config /etc/etekcity-bp-daemon/config.ini --check-config
```

#### systemd service

```bash
sudo cp systemd/etekcity-bp-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now etekcity-bp-daemon
journalctl -u etekcity-bp-daemon -f
```

### Scheduled report generation

Optional and not enabled by default. Generates a timestamped PDF into
`/var/lib/etekcity-bp-daemon/reports/` on a schedule:

```bash
sudo cp systemd/etekcity-bp-report-generate.service systemd/etekcity-bp-report-generate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now etekcity-bp-report-generate.timer
```

Defaults to `OnCalendar=weekly`. Configure via the `ETEKCITY_CONFIG` and
`ETEKCITY_REPORT_DIR` environment variables in the `.service` unit rather
than flags, since the timer invokes it with a fixed command line.

### Alerting

Also optional and not enabled by default. `etekcity-bp-alert-check` checks
every user slot's most recent reading for three conditions and notifies via
[Apprise](https://github.com/caronc/apprise) (100+ supported services:
Discord, Telegram, Slack, email, Pushover, generic webhooks, etc.) when
triggered:

- **Staleness**: no reading in over `stale_after_days` days.
- **Hypertensive-crisis range**: systolic/diastolic at or above
  `crisis_systolic_mmhg`/`crisis_diastolic_mmhg`.
- **Irregular heartbeat**: the device's irregular-heartbeat flag was set on
  the latest reading, if `alert_on_irregular_heartbeat = yes`.

```ini
[alerting]
enabled = yes
apprise_urls = tgram://bot_token/chat_id, mailto://user:password@gmail.com
stale_after_days = 2
crisis_systolic_mmhg = 180
crisis_diastolic_mmhg = 120
alert_on_irregular_heartbeat = yes
```

Run it periodically with the bundled timer:

```bash
sudo cp systemd/etekcity-bp-alert-check.service systemd/etekcity-bp-alert-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now etekcity-bp-alert-check.timer
```

Defaults to `OnCalendar=hourly`. A repeat staleness alert is throttled to at
most once per day while the condition persists; a crisis-range or
irregular-heartbeat alert only fires once per newly-arrived reading, not on
every check. State is tracked per user slot in `alerting.state_path`
(default `/var/lib/etekcity-bp-daemon/alert-state.json`). Delete it to
reset throttling. `--check-config` reports whether `[alerting]` is enabled
and how many URLs it parsed, without actually sending anything.

If a reading is tagged with a profile (see [Profiles](#profiles)), that
profile's `[profile.<name>]` section can override the destination and
thresholds just for its own alerts; see
[Per-profile alert routing](#per-profile-alert-routing).

### HTTP API

Also optional and not enabled by default. `etekcity-bp-api` runs a small
read-only HTTP server exposing the same data as the other tools. It reads
the SQLite database directly and works whether or not the daemon is
currently running.

```ini
[api]
enabled = yes
host = 127.0.0.1
port = 8080
token =
```

```bash
sudo cp systemd/etekcity-bp-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now etekcity-bp-api.service
```

Endpoints:

All routes are versioned under `/api/v1/`.

| Method & path | Description |
|---|---|
| `GET /api/v1/health` | Unauthenticated liveness check: `{"status": "ok", "version": "..."}`. |
| `GET /api/v1/capabilities` | Unauthenticated description of what this daemon measures, its profile model, timestamp semantics, and MQTT status. |
| `GET /api/v1/latest[?address=...&profile=...]` | Most recent reading per user slot, as JSON. |
| `GET /api/v1/report[?format=pdf\|csv&period=...&from=...&to=...&address=...&profile=...]` | Generates a report on demand using the same `[report]` config as `etekcity-bp-report`, returned as a file download. |
| `GET`/`POST /api/v1/assign-profile?id=...&profile=...[&confirm=1]` | Tags a reading with a profile name (see [Profiles](#profiles)). |

```bash
curl http://127.0.0.1:8080/api/v1/latest
curl -o report.pdf "http://127.0.0.1:8080/api/v1/report?period=30d"
```

**There's no TLS built in.** `host` defaults to `127.0.0.1` (loopback only)
for a reason: don't bind it to `0.0.0.0` or a LAN-facing interface without
putting a reverse proxy (with TLS and its own auth) in front of it. Setting
`api.token` requires an `Authorization: Bearer <token>` header on every
endpoint except `/api/v1/health` and `/api/v1/capabilities`, which is worth
doing even on loopback if other local users/processes on the same host
shouldn't see readings:

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/api/v1/latest
```

If `api.host` isn't a loopback address and `api.token` is blank, both
`etekcity-bp-api` (at startup) and `--check-config` print a warning. It's
not blocked outright, since a reverse proxy handling auth in front is a
legitimate setup, but forgetting to set a token before exposing the API on
the LAN is a plausible mistake worth surfacing rather than letting it pass
silently.

### Profiles

For a device shared by more than one person: `[profiles]` asks "who was
this?" after each reading and tags it. The device's own user slot (0 or 1)
can't be used for this: it only ever reports one of two values no matter
how many people actually share the device, so it can't tell a third or
fourth person apart from whoever normally uses that slot. This mirrors
[`etekcity-scale-daemon`](https://github.com/bonelifer/etekcity-scale-daemon)'s
profile system rather than relying on the device's hardware slots, so any
number of people can share one monitor.

```ini
[profiles]
enabled = yes
names = Alice, Bob, Charlie
```

Two delivery paths, chosen automatically based on whether `[api]` is enabled:

- **`[api]` enabled**: an [ntfy](https://ntfy.sh) notification (Android/iOS
  apps, or any browser) with one HTTP action button per name in
  `profiles.names`. Tapping a button hits this API's `/api/v1/assign-profile`
  endpoint directly, tagging that specific reading. Requires
  `profiles.ntfy_url` (and `profiles.api_base_url` pointing at wherever the
  API is actually reachable from your phone/desktop; `127.0.0.1` only works
  if ntfy and the API run on the same machine).
- **`[api]` disabled**: a local [dunstify](https://dunst-project.org) prompt
  instead, since ntfy's action buttons would have nothing to call back to
  without the API running. This resolves synchronously and tags the reading
  directly, no network round-trip. It needs the `dunst` notification daemon
  and a real desktop/D-Bus session, which makes it a better fit for running
  the daemon on your own desktop or laptop than an unattended headless Pi
  (the usual deployment for this daemon), so ntfy is the practical choice
  there.

```bash
curl "http://127.0.0.1:8080/api/v1/assign-profile?id=42&profile=Alice"
```

If `profiles.assign_window_seconds` is set, this fails with `409` for a
reading older than that window: a safety net for delayed ntfy notifications
(tapped long after connectivity returns, potentially tagging a now-stale
reading someone's forgotten about) rather than a limit on manual
corrections. Add `&confirm=1` to tag an old reading on purpose:

```bash
curl "http://127.0.0.1:8080/api/v1/assign-profile?id=42&profile=Alice&confirm=1"
```

`--check-config` cross-checks `profiles.names` against the database: if a
name was removed or renamed but readings tagged with the old name still
exist, it prints a warning (not an error; the exit code stays `0`) so that
history doesn't just silently stop being explainable.

#### Per-profile report personalization

Give a profile its own `[profile.<name>]` section (name/email, notes, report
preferences, and/or a blood-pressure goal), and `etekcity-bp-report
--profile <name>` / the API's `?profile=` will use it:

```ini
[profile.Alice]
name = Alice Smith
email = alice@example.com
notes = On lisinopril 10mg
unit = mmhg
goal_systolic_mmhg = 130
goal_diastolic_mmhg = 80
goal_pulse_bpm = 70
```

- `name`/`email`/`notes` print below the report title, handy when handing
  a printed report to a doctor (`notes` for clinical context like current
  medication).
- `unit`/`date_format`/`page_size` each independently override the matching
  `[report]` setting for this profile's reports only, so one household
  member can see mmHg while another sees kPa.
- `goal_systolic_mmhg`/`goal_diastolic_mmhg`/`goal_pulse_bpm` (any subset,
  independently) enable `report.include_goal_progress = yes`: the current
  average against each goal, and whether it's trending toward or away from
  it based on a linear fit across the report's date range. Since a BP goal
  is a ceiling ("keep it under X/Y"), a falling trend is favorable
  regardless of whether the current average happens to be over or under it
  yet.

#### Per-profile alert routing

The same `[profile.<name>]` section can also override `[alerting]` for
alerts triggered by that profile's readings (untagged readings always use
the global values):

```ini
[profile.Alice]
apprise_urls = tgram://bot_token/alice_chat_id
stale_after_days = 1
alert_on_irregular_heartbeat = yes
```

- `apprise_urls` **replaces** the global `[alerting] apprise_urls` for this
  profile's alerts rather than adding to it, so Alice's alerts go to her
  phone and Bob's go to his instead of everyone seeing a shared feed.
  Leave blank to just use the global list.
- `stale_after_days`/`alert_on_irregular_heartbeat` override the matching
  `[alerting]` value for this profile only; leave blank to inherit it.
- The hypertensive-crisis thresholds (`crisis_systolic_mmhg`/
  `crisis_diastolic_mmhg`) are never overridden per profile: they're a
  fixed medical definition, not a personal preference.

None of this is required. A profile with no `[profile.<name>]` section at
all still tags and reports/alerts normally, just without the
personalization. `--check-config` validates every configured profile's
section and reports how many parsed cleanly (`details_valid=N/M`).

### Docker

```bash
mkdir -p config data
cp config/etekcity-bp-daemon.ini.example config/config.ini
"$EDITOR" config/config.ini
docker compose up -d
```

Or use the prebuilt image instead of `docker compose build`'s local build:
`ghcr.io/bonelifer/etekcity-bp-daemon:latest`. CI builds this image on every
push to `main`, runs `--check-config` and a report generation inside it, and
pushes it to GHCR, so the image itself is exercised, but only its CLI
tooling, not a live BLE connection (see below).

BLE access from inside a container needs the host's D-Bus system bus and
Bluetooth adapter, which `docker-compose.yml` reaches via `network_mode:
host` and a `/var/run/dbus` bind mount. **This part is unverified**: BLE
from containers is finicky across host setups, and the bare-metal `systemd`
install is the well-tested path. If Docker doesn't see the adapter, try
running the container with `--privileged` or check that BlueZ's D-Bus
service is reachable at the mounted socket.

## Manual usage

### On-demand capture instead of a long-running service

```bash
etekcity-bp-daemon --config /etc/etekcity-bp-daemon/config.ini --once --once-timeout 60
```

Connects, waits up to `--once-timeout` seconds for a single reading, records
it, and exits. Exit code is `1` if no reading arrived in time. For when you'd
rather not run the daemon continuously: start it by hand right before (or
while) taking a reading, instead of taking the reading and finding nothing
was listening.

## Database schema

One `readings` table, one row per completed measurement:

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key |
| `recorded_at` | TEXT | ISO-8601 UTC timestamp |
| `address` | TEXT | Device BLE address |
| `user` | INTEGER | Device user slot (0 or 1) |
| `profile` | TEXT | Tagged profile name (see [Profiles](#profiles)), NULL until answered |
| `systolic_mmhg`, `diastolic_mmhg` | INTEGER | Pressure in mmHg |
| `systolic_kpa`, `diastolic_kpa` | REAL | Pressure in kPa |
| `pulse_bpm` | INTEGER | Pulse rate |
| `irregular_heartbeat`, `motion_detected` | INTEGER | 0/1 flags |
| `display_unit` | TEXT | Device's display unit at time of reading |
| `error_code` | TEXT | Device error code ("OK" if none) |

## Reports

```bash
# Every reading on record
etekcity-bp-report --config /etc/etekcity-bp-daemon/config.ini

# Preset ranges: 7d, 30d, 90d, 1y, all (default: all)
etekcity-bp-report --config /etc/etekcity-bp-daemon/config.ini --period 30d

# Explicit date range (--to defaults to now if omitted)
etekcity-bp-report --config /etc/etekcity-bp-daemon/config.ini --from 2026-01-01 --to 2026-03-01

# Point directly at a database file instead of a config
etekcity-bp-report --db /var/lib/etekcity-bp-daemon/readings.db --format csv --output report.csv

# Restrict to one profile
etekcity-bp-report --config /etc/etekcity-bp-daemon/config.ini --profile Alice
```

PDF reports include a systolic/diastolic trend chart, a reading table shaded
by AHA blood-pressure category, and (if `report.include_summary = yes`) an
average/min/max summary with a category breakdown, handy to print and
bring to a doctor's appointment. `--profile <name>` (requires `--config`)
also personalizes the report from that profile's `[profile.<name>]` section;
see [Per-profile report personalization](#per-profile-report-personalization).

`report.include_chart`/`include_table` independently toggle the chart and
table off if you don't want them, and `report.table_layout` picks the
table's shape: `full` (one row per reading, the default), `compact` (same
per-reading detail, packed into 2 side-by-side column groups), or `rollup`
(one row per week/month: avg/min/max, reading count, and the worst AHA
category seen that period). For a long history, `rollup` paired with the
chart is generally more useful than paging through a year of individual
readings.

See [samples/](samples/) for a rendered PDF of every layout/unit/date-format
combination, plus the toggle and goal-progress demos above:
[samples/combined/](samples/combined/) for the whole household sharing one
device, [samples/single/](samples/single/) for a single person's report to
bring to a doctor's appointment.

**Reports spanning more than one person are split per person, not blended.**
If a report's rows include more than one distinct profile (or untagged
"User 1"/"User 2" without profiles), averaging everyone's systolic/diastolic
together into one number would be medically meaningless, so the chart gets
one colored line pair per person (with a legend), the summary prints one
avg/min/max block per person, and the `rollup` layout adds a "Who" column
and buckets by `(period, person)` instead of just `(period)`. The full/
compact per-reading tables already label each row via the "Who" column
(`report.include_profile = yes`), so they're unaffected. This is the right
default for a household report shared by everyone using the device, but if
you want a single person's data instead (e.g. to bring to a doctor's
appointment), pass `--profile <name>` (or `?profile=` via the API) rather
than filtering the combined report after the fact.

## Pruning old data

```bash
# See how many readings older than 365 days would be deleted
etekcity-bp-prune --config /etc/etekcity-bp-daemon/config.ini --older-than 365

# Actually delete them (also reclaims disk space with VACUUM)
etekcity-bp-prune --config /etc/etekcity-bp-daemon/config.ini --older-than 365 --yes
```

## MQTT

```ini
[mqtt]
enabled = yes
host = mqtt.example.com
topic_prefix = etekcity_bp_daemon
```

Each reading publishes as JSON to `<topic_prefix>/<device address>/state`. A
broker outage is logged and non-fatal; it never blocks local recording to
SQLite.

## Troubleshooting

- **Device never discovered**: make sure it's powered on (press `MEM`) while
  the daemon is scanning, and that no other app (e.g. VeSync, nRF Connect)
  is already connected to it: the device only accepts one connection at a
  time.
- **`No Bluetooth scanner available`**: check `bluetoothctl` shows an
  adapter, and that the `etekcity-bp-daemon` system user is in the
  `bluetooth` group (the systemd unit sets `SupplementaryGroups=bluetooth`).
- **Config errors**: run `--check-config` for a section-by-section report of
  what's wrong.

## Acknowledgments

- Built on [`etekcity-bp-ble`](https://github.com/bonelifer/etekcity-bp-ble),
  which itself is based on the protocol decoding in
  [EdLeckert/ha_etekcity_blood_pressure_monitor](https://github.com/EdLeckert/ha_etekcity_blood_pressure_monitor).
- Project layout modeled on
  [`etekcity-scale-daemon`](https://github.com/bonelifer/etekcity-scale-daemon).
- Code review, implementation, and documentation assisted by
  [Claude](https://www.anthropic.com/claude).

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/bonelifer/etekcity-bp-daemon/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/bonelifer/etekcity-bp-daemon/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
