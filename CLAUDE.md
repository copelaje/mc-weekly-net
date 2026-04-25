# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Install dependencies
pip install -r requirements.txt

# Run directly
python net_bot.py

# Run in Docker (production)
docker compose up --build -d

# View logs
docker compose logs -f
```

`config.yaml` is bind-mounted into the container at `/app/config.yaml`; edit it on the host and restart to apply changes.

## Architecture

`net_bot.py` is a single-file Python bot that bridges a [Corescope](https://corescope.csramsh.org) mesh-radio channel to Discord webhooks during a scheduled weekly amateur radio net.

**Data flow:**
1. A `BackgroundScheduler` (APScheduler) fires `start_net` / `end_net` at the configured day/time, opening and closing a time window.
2. The main thread polls the Corescope REST API every `poll_interval_seconds`.
3. `process_messages` deduplicates by `packetHash`, filters messages, relays check-ins to Discord via the `checkin_webhook`, and optionally broadcasts net open/close announcements back to a meshcore channel.

**`NetState`** is the shared mutable object (protected by a `threading.Lock`) that tracks whether the window is open and which participants have checked in this session.

**Deduplication** is persisted to `data/mc-seen_packets.txt` (one hash per line, trimmed to the last 500 lines when it exceeds 1000). On startup the bot preloads this file *and* the current API response so it doesn't replay historical messages after a restart. If the bot starts inside the scheduled window (e.g. after a crash), `within_window()` is called and `state.resume_window()` is used instead of `state.open_window()` to avoid resetting in-progress participant state.

## Configuration

All runtime behaviour is driven by `config.yaml`:

| Section | Key fields |
|---|---|
| `corescope` | `base_url`, `channel` to read from, `poll_interval_seconds` |
| `discord` | `checkin_webhook` (per check-in), `announce_webhook` (net open/close) |
| `meshcore` | `enabled` flag, `channel` to POST announcements back to |
| `schedule` | `timezone` (pytz string), `day_of_week`, `start_time` / `end_time` (24h) |
| `net` | `start_message`, `end_message_template` (`{count}` placeholder), `checkin_pattern` regex, `require_pattern_match` |

`config.yaml` is **not** copied into the Docker image — it is always mounted from the host, so secrets (webhook URLs) stay out of the image.
