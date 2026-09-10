# Agent / headless sync

Use the `intervalssync` CLI (headless companion to **Intervals Sync**) to sync
cycling activities to intervals.icu without the GUI. Sources: **iGPSPORT**
(default) or **Bryton Active** (`--source bryton`).
Also uploads planned workouts from intervals.icu to **iGPSPORT** or **Bryton Active**.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed

No repository clone or virtual environment is needed. `uvx` downloads the
latest stable `intervalssync` release from PyPI into an isolated environment.
The explicit `@latest` refreshes uv's cached package metadata on every run.

## User setup (human — not the agent)

Agents cannot write secrets. The **user** must add credentials to a **`.env` file
only** — never use `hermes config set` for email or password (those end up in
`config.yaml` in plaintext).

### iGPSPORT sync

```bash
echo 'INTERVALSSYNC_IGPSPORT_USER=you@example.com' >> ~/.hermes/.env
echo 'INTERVALSSYNC_IGPSPORT_PASSWORD=your-password' >> ~/.hermes/.env
echo 'INTERVALSSYNC_INTERVALS_API_KEY=your-intervals-api-key' >> ~/.hermes/.env
chmod 600 ~/.hermes/.env
```

For **China-region** accounts ([app.igpsport.cn](https://app.igpsport.cn/login)), use your phone number as the username and add:

```bash
echo 'INTERVALSSYNC_IGPSPORT_REGION=china' >> ~/.hermes/.env
```

### Bryton sync

```bash
echo 'INTERVALSSYNC_BRYTON_EMAIL=you@example.com' >> ~/.hermes/.env
echo 'INTERVALSSYNC_BRYTON_PASSWORD=your-password' >> ~/.hermes/.env
echo 'INTERVALSSYNC_INTERVALS_API_KEY=your-intervals-api-key' >> ~/.hermes/.env
chmod 600 ~/.hermes/.env
```

For a named Hermes profile, use `$HERMES_HOME/.env` (that profile's directory).

**intervals.icu API key:** intervals.icu → Settings → Developer.

Verify:

```bash
uvx --python 3.13 intervalssync@latest check                   # iGPSPORT keys
uvx --python 3.13 intervalssync@latest check --source bryton   # Bryton keys
```

Optional: `--env-file /path/to/.env` on the **intervalssync** command (e.g.
`uvx --python 3.13 intervalssync@latest sync-zones --env-file .env`).

Check which release the agent will run:

```bash
uvx --python 3.13 intervalssync@latest --version
```

## Agent invocation

### Activity sync

```bash
uvx --python 3.13 intervalssync@latest sync --json                   # iGPSPORT
uvx --python 3.13 intervalssync@latest sync --source bryton --json   # Bryton Active
```

- **Progress** on **stderr**; **result** JSON on **stdout**.
- **Exit code:** `0` success · `1` sync error · `2` missing credentials

iGPSPORT success example:

```json
{
  "ok": true,
  "source": "igpsport",
  "listed": 5,
  "uploaded": 2,
  "skipped": 3,
  "failed": 0,
  "downloaded": 2,
  "activities": [{"ride_id": 123, "title": "Morning ride", "start_time": "2026-06-15 08:00:00"}]
}
```

Bryton success uses `"source": "bryton"` and `activity_id` instead of `ride_id`.

### Workout upload (intervals.icu → iGPSPORT or Bryton)

```bash
uvx --python 3.13 intervalssync@latest upload-workouts --json                   # iGPSPORT
uvx --python 3.13 intervalssync@latest upload-workouts --source bryton --json   # Bryton Active
```

Requires credentials for the chosen target. Same exit-code rules.

### Zone sync (intervals.icu → iGPSPORT profile)

```bash
uvx --python 3.13 intervalssync@latest sync-zones --env-file .env --json
```

- **iGPSPORT-only** (no `--source` flag)
- Reads FTP, LTHR, max HR, power zones, and HR zones from intervals.icu `sport-settings/Ride` (override with `--sport`), plus athlete weight (`icu_weight`) rounded to whole kg
- Writes thresholds/zones via `UpdatePersonalIntervalInfo`, and weight via `User/UpdatePersonalUserInfo` (the endpoint the app profile editor uses)
- Progress on **stderr**; result JSON on **stdout**; same exit codes as other commands

Success example:

```json
{
  "ok": true,
  "source": "igpsport",
  "before": {
    "ftp": 240,
    "lthr": 153,
    "mhr": 193,
    "weight": 79.0,
    "power_zones": "0-132 | 132-180 | …",
    "hr_zones": "0-120 | …"
  },
  "ftp": 242,
  "lthr": 176,
  "mhr": 193,
  "weight": 76.0,
  "power_zones": "0-133 | 133-182 | …",
  "hr_zones": "0-120 | 120-146 | …",
  "after": {
    "ftp": 242,
    "lthr": 176,
    "mhr": 193,
    "weight": 76.0,
    "power_zones": "0-133 | 133-182 | …",
    "hr_zones": "0-120 | 120-146 | …"
  }
}
```

### Verified rider settings (intervals.icu → iGPSPORT)

Use this command when intervals.icu must own the cycling profile values and
iGPSPORT is only the destination. This command is not in the current upstream
PyPI release (`v0.9.3`). The examples intentionally execute the immutable fork
revision reviewed and deployed by `relay-to-intervals`:

```bash
# All supported fields, status-only output, no writes
uvx --python 3.13 \
  --from "git+https://github.com/KirillSugonyakin/intervalssync.git@81f49ad5356aa41609214bae34bc682497c14ead" \
  intervalssync sync-rider-settings \
  --env-file .env --sport Ride --dry-run --json

# Selected zone groups; show values only for this explicit dry-run
uvx --python 3.13 \
  --from "git+https://github.com/KirillSugonyakin/intervalssync.git@81f49ad5356aa41609214bae34bc682497c14ead" \
  intervalssync sync-rider-settings \
  --env-file .env --sport Ride \
  --fields hr_zones,power_zones --dry-run --show-values --json

# Apply all supported fields and verify iGPSPORT read-back
uvx --python 3.13 \
  --from "git+https://github.com/KirillSugonyakin/intervalssync.git@81f49ad5356aa41609214bae34bc682497c14ead" \
  intervalssync sync-rider-settings \
  --env-file .env --sport Ride --json
```

Supported fields are `ftp`, `power_zones`, `max_hr`, `lthr`, `hr_zones`,
`resting_hr`, `weight`, `height`, `birth_date`, and `sex`. Omit `--fields`, or
pass a blank value, to select all fields. `power_zones` automatically includes
`ftp`; `hr_zones` automatically includes `lthr` and `max_hr`. Duplicate, empty,
or unknown field names are configuration errors. Only cycling `Ride` is
supported; other sports are intentionally reserved for later generic support.

The strict adapter recognizes a direct five-zone HR scheme, Friel seven-zone HR,
and Coggan seven-zone power. Friel HR maps zones 1–4 directly and combines the
upper zones into iGPSPORT zone 5 in LTHR mode. Coggan power retains seven slots,
uses FTP percentages with half-up rounding, and preserves the destination's
terminal cap. Unknown or malformed schemes fail closed instead of interpolating.

Synthetic conversion example: with Friel HR upper bounds
`[120,145,165,175,185,190,195]` and maximum HR `195`, iGPSPORT receives five
ends `[120,145,165,175,195]`; the three upper Friel bands become one fifth band.
With FTP `250`, Coggan percentages `[55,75,90,105,120,150,999]`, and a fetched
destination cap of `2500`, iGPSPORT receives
`[138,188,225,263,300,375,2500]`. Positive half-watts round upward, the `999`
open marker is never multiplied by FTP, and the existing terminal cap is kept.

Missing Intervals values never clear iGPSPORT fields. Writes are grouped by the
iGPSPORT interval and personal-profile endpoints, with at most one write per
changed group. Each successful write is fetched again and compared exactly; a
2xx response with a mismatch is `verify_failed`. A second unchanged run performs
no write.

Normal JSON is status-only. It contains requested/effective fields, per-field
statuses, recognized zone models, `zone_adapter_version`, and the numeric
`selected`, `updated`, `verified`, `unchanged`, `source_missing`, and `failed`
counters. Source/current/desired values appear only when both `--dry-run` and
`--show-values` are supplied. `--show-values` without `--dry-run` is rejected.

Possible field statuses are `unchanged`, `would_update`, `verified`,
`source_missing`, `invalid`, `write_failed`, and `verify_failed`. Exit code `0`
means no field failed; `1` means an upstream/write/verification failure; `2`
means invalid CLI configuration or credentials.

## Optional flags

| Flag | Purpose |
|------|---------|
| `--source {igpsport,bryton}` | Activity source or workout upload target (default: igpsport) |
| `--env-file PATH` | Override secrets file |
| `--json` | JSON on stdout |
| `--sport TYPE` | intervals.icu sport-settings key (`sync-zones`); exactly `Ride` for `sync-rider-settings` |
| `--fields LIST` | Rider fields for `sync-rider-settings`; omitted/blank means all |
| `--dry-run` | Compute rider changes without POSTing |
| `--show-values` | Include selected values; requires rider `--dry-run` |

`sync` flags: `--max-activities`, `--force-resync`, `--activity-type`, `--download-dir`, `--keep-files`.

## Subcommands

| Command | Description |
|---------|-------------|
| `intervalssync sync` | Download recent rides → upload to intervals.icu |
| `intervalssync upload-workouts` | Planned workouts → iGPSPORT or Bryton (`--source`) |
| `intervalssync sync-zones` | Push thresholds + zones from intervals.icu → iGPSPORT profile |
| `intervalssync sync-rider-settings` | Selective, verified cycling profile sync from intervals.icu → iGPSPORT |
| `intervalssync check` | Validate `.env` keys (no network) |

`@latest` means the newest stable release published to PyPI, not the newest
commit on `master`. Maintainers must cut a new stable tag before agents receive
a merged fix.

## CLI config

Non-secret defaults in `intervalssync-cli` `config.json` (`platformdirs`). Secrets never go there.

## What it does

### Activity sync

- **iGPSPORT:** list → FIT URL → download → upload (`igpsport_{ride_id}` external_id).
- **Bryton:** DDP login → activity list → `GET /api/activity?id=…` FIT → upload (`bryton_{id}` external_id).
- Skips already on intervals.icu unless `--force-resync`.
- **Dropbox** is GUI-only (Settings → connect Dropbox, enable upload). iGPSPORT
  uses `ride-0-YYYY-MM-DD-HH-MM-SS.fit` or `igpsport_{id}.fit`; Bryton uses
  `YYMMDDHHMMSS.fit` or `bryton_{id}.fit`.

### Workout upload

Same as GUI **Upload to iGPSPORT** / **Upload to Bryton** — intervals.icu calendar → custom workouts on the chosen device platform.

For iGPSPORT, the CLI persists a hashed logical identity and normalized export
fingerprint in `workout_records`. A changed Intervals plan updates the same
iGPSPORT workout ID. If that remote workout is manually deleted while its plan
is still inside the configured upload window, the next run recreates the latest
version. Once a plan is outside the window, a missing remote workout is not
recreated and its stale mapping is pruned after a complete remote lookup.

Add `[skip-igp]` to the Intervals event name or description to suppress create,
update, and recreation. The marker also applies with `--force-resync`; removing
it resumes normal sync. `[ignore-igp]` is ordinary text and is not a synonym.

The Intervals event description takes precedence over the nested workout
description and is limited to 500 characters. Step classification follows
explicit warmup, cooldown, rest, recovery, active, and interval metadata. The
sync does not infer warmup or cooldown from position or power.

The iGPSPORT JSON result includes `uploaded`, `updated`, `recreated`, `skipped`,
`conflicted`, `failed`, `description_truncated`, `uploaded_map`, and
`synced_map`. A conflict or incomplete Intervals/iGPSPORT lookup fails closed:
the command preserves mappings and performs no ambiguous write.

### Zone sync

Reads FTP, LTHR, max HR, power/HR zones, and weight from intervals.icu and writes them to the iGPSPORT profile (CLI only for now).
