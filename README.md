# Weekly Net Bot

Bridges a [Corescope](https://corescope.csramsh.org) mesh-radio channel to Discord during a scheduled weekly amateur radio net. Optionally broadcasts net open/close announcements back to a MeshCore channel via a companion relay service.

## How it works

1. A scheduler fires at the configured start/end times each week, opening and closing the relay window.
2. While the window is open, the bot polls the Corescope REST API for new messages.
3. Messages that match the check-in pattern (or all messages, if `require_pattern_match` is false) are forwarded to Discord.
4. Net open/close announcements are sent to a Discord webhook and, optionally, broadcast to a MeshCore channel.

---

## Deployment options

### Option A — Bot only (no MeshCore broadcasting)

Use this when you only need Discord relaying and MeshCore broadcasting is disabled (`meshcore.enabled: false`).

```
┌─────────────┐     polls      ┌───────────┐
│  net_bot    │ ─────────────► │ Corescope │
│  container  │                └───────────┘
│             │   webhooks     ┌───────────┐
│             │ ─────────────► │  Discord  │
└─────────────┘                └───────────┘
```

### Option B — Bot + relay on the same machine

Use this when the MeshCore companion is reachable from the same host.

```
┌─────────────┐     polls      ┌───────────┐
│  net_bot    │ ─────────────► │ Corescope │
│  container  │                └───────────┘
│             │   webhooks     ┌───────────┐
│             │ ─────────────► │  Discord  │
│             │                └───────────┘
│             │   HTTP + token ┌───────────┐         ┌───────────┐
│             │ ─────────────► │   relay   │ ──TCP─► │ MeshCore  │
└─────────────┘                │ container │         │ companion │
                               └───────────┘         └───────────┘
```

### Option C — Bot and relay on separate machines

Use this when the MeshCore companion is only reachable from a different host (e.g. a Raspberry Pi next to the radio).

```
  Machine 1                          Machine 2
┌─────────────┐                    ┌───────────┐
│  net_bot    │  HTTPS + token     │   relay   │         ┌───────────┐
│  container  │ ─────────────────► │ container │ ──TCP─► │ MeshCore  │
└─────────────┘                    └───────────┘         │ companion │
                                                         └───────────┘
```

---

## Setup

### 1. Configure the bot

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:

| Section | Key | Description |
|---|---|---|
| `corescope` | `base_url` | Base URL of your Corescope instance |
| `corescope` | `channel` | Channel name to read check-ins from |
| `corescope` | `poll_interval_seconds` | How often to poll (default 15) |
| `discord` | `checkin_webhook` | Webhook URL for individual check-ins |
| `discord` | `announce_webhook` | Webhook URL for net open/close announcements |
| `meshcore` | `enabled` | Set `true` to enable MeshCore broadcasting |
| `meshcore` | `relay_url` | URL of the relay service (see below) |
| `meshcore` | `relay_token` | Shared secret matching `RELAY_TOKEN` on the relay |
| `meshcore` | `group` | MeshCore channel/group name to broadcast to |
| `schedule` | `timezone` | pytz timezone string, e.g. `America/New_York` |
| `schedule` | `day_of_week` | `mon` `tue` `wed` `thu` `fri` `sat` `sun` |
| `schedule` | `start_time` | Net start time in 24h local time, e.g. `19:00` |
| `schedule` | `end_time` | Net end time in 24h local time, e.g. `20:00` |
| `net` | `start_message` | Message sent when net opens |
| `net` | `end_message_template` | Closing message; `{count}` is replaced with check-in count |
| `net` | `checkin_pattern` | Regex a message must match to be treated as a check-in |
| `net` | `require_pattern_match` | `false` = relay everything; `true` = only pattern matches |

### 2. MeshCore group names

The `meshcore.group` value determines how the relay handles the channel:

| Value | Behaviour |
|---|---|
| `public` | Uses the standard MeshCore public channel (fixed known key). Auto-provisioned if not present. |
| `#name` | Hashtag room — key is derived from the name via SHA-256. Auto-provisioned if not present. |
| anything else | Private channel — must already be configured on the companion. Returns an error if not found. |

### 3. Generate a relay token

Only needed when `meshcore.enabled: true`.

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Put the output in both `config.yaml` (`meshcore.relay_token`) and the `.env` file (`RELAY_TOKEN`).

### 4. Create a `.env` file for the relay

Only needed when running the relay container (Options B and C).

```bash
cp env.example .env
```

Edit `.env` and fill in the values:

| Variable | Description |
|---|---|
| `RELAY_TOKEN` | Shared secret from step 3 — must match `meshcore.relay_token` in `config.yaml` |
| `COMPANION_HOST` | IP address or hostname of the MeshCore companion device |
| `COMPANION_PORT` | TCP port the companion listens on (default `4403`) |

---

## Running

### Option A — Bot only

```bash
docker compose up -d
docker compose logs -f
```

### Option B — Bot + relay on the same machine

```bash
docker compose --profile relay up -d
docker compose logs -f
```

Set `meshcore.relay_url` to `http://relay:8080/send` in `config.yaml` (the Docker service name resolves automatically within the shared network).

### Option C — Relay on a separate machine

On the relay machine, copy these files:
- `docker-compose.relay.yml`
- `Dockerfile.relay`
- `relay_requirements.txt`
- `relay_service.py`
- `.env`

Then:

```bash
docker compose -f docker-compose.relay.yml up -d
docker compose -f docker-compose.relay.yml logs -f
```

On the bot machine, set `meshcore.relay_url` in `config.yaml` to `http://<relay-machine-ip>:8080/send` and ensure port 8080 is reachable from the bot host.

### Running without Docker

```bash
pip install -r requirements.txt
python net_bot.py
```

For the relay:

```bash
pip install -r relay_requirements.txt
RELAY_TOKEN=... COMPANION_HOST=... uvicorn relay_service:app --host 0.0.0.0 --port 8080
```

---

## Applying config changes

`config.yaml` is bind-mounted into the container — edit it on the host and restart to apply:

```bash
docker compose restart net-bot
```
