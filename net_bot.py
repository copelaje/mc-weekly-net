import os
import re
import time
import logging
import threading
from datetime import datetime
from urllib.parse import quote

import pytz
import requests
import yaml
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SEEN_FILE = "data/mc-seen_packets.txt"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path="config.yaml"):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    required = [
        ("corescope", "base_url"),
        ("corescope", "channel"),
        ("discord", "checkin_webhook"),
        ("discord", "announce_webhook"),
        ("schedule", "timezone"),
        ("schedule", "day_of_week"),
        ("schedule", "start_time"),
        ("schedule", "end_time"),
    ]
    for section, key in required:
        val = cfg.get(section, {}).get(key, "")
        if not val:
            raise ValueError(f"config.yaml: [{section}] {key} is required")
    return cfg

# ---------------------------------------------------------------------------
# Seen-packet file persistence
# ---------------------------------------------------------------------------

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE) as f:
        return {line.strip() for line in f if line.strip()}

def save_seen(packet_hash):
    with open(SEEN_FILE, "a") as f:
        f.write(packet_hash + "\n")

def trim_seen_file(max_lines=1000):
    if not os.path.exists(SEEN_FILE):
        return
    with open(SEEN_FILE) as f:
        lines = f.readlines()
    if len(lines) > max_lines:
        with open(SEEN_FILE, "w") as f:
            f.writelines(lines[-500:])

# ---------------------------------------------------------------------------
# Corescope API
# ---------------------------------------------------------------------------

def build_messages_url(base_url, channel):
    return f"{base_url}/{quote(channel, safe='')}/messages"

def fetch_messages(base_url, channel):
    url = build_messages_url(base_url, channel)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json().get("messages", [])
    except Exception as e:
        log.warning("Fetch error: %s", e)
        return []

def post_meshcore_message(base_url, channel, text):
    url = build_messages_url(base_url, channel)
    try:
        resp = requests.post(url, json={"text": text}, timeout=10)
        log.info("Meshcore POST %s -> %d %s", url, resp.status_code, resp.text[:200])
        resp.raise_for_status()
    except Exception as e:
        log.warning("Meshcore send error: %s", e)

# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def send_discord(webhook_url, content, username=None):
    payload = {"content": content}
    if username:
        payload["username"] = username
    try:
        requests.post(webhook_url, json=payload, timeout=5)
    except Exception as e:
        log.warning("Discord error: %s", e)

# ---------------------------------------------------------------------------
# Net state (shared between scheduler callbacks and poll loop)
# ---------------------------------------------------------------------------

class NetState:
    def __init__(self):
        self._lock = threading.Lock()
        self.window_open = False
        # packetHash -> sender string; tracks unique check-ins this window
        self.participants: dict[str, str] = {}

    def open_window(self):
        with self._lock:
            self.window_open = True
            self.participants = {}
            log.info("Net window opened")

    def resume_window(self):
        """Open window without resetting state — used when starting mid-window."""
        with self._lock:
            self.window_open = True
            log.info("Resumed mid-window; relaying will begin immediately")

    def close_window(self):
        with self._lock:
            self.window_open = False
            count = len(self.participants)
            log.info("Net window closed; %d participant(s)", count)
            return count

    def is_open(self):
        with self._lock:
            return self.window_open

    def add_participant(self, packet_hash, sender):
        with self._lock:
            self.participants[packet_hash] = sender

    def count(self):
        with self._lock:
            return len(self.participants)

# ---------------------------------------------------------------------------
# Window check
# ---------------------------------------------------------------------------

_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

def within_window(cfg: dict) -> bool:
    tz = pytz.timezone(cfg["schedule"]["timezone"])
    now = datetime.now(tz)
    if now.weekday() != _DOW[cfg["schedule"]["day_of_week"]]:
        return False
    start_h, start_m = (int(x) for x in cfg["schedule"]["start_time"].split(":"))
    end_h, end_m = (int(x) for x in cfg["schedule"]["end_time"].split(":"))
    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=end_m, second=59, microsecond=0)
    return start <= now <= end

# ---------------------------------------------------------------------------
# Scheduler callbacks
# ---------------------------------------------------------------------------

def start_net(state: NetState, cfg: dict, seen_packets: set):
    state.open_window()
    net_cfg = cfg["net"]
    discord_cfg = cfg["discord"]

    send_discord(discord_cfg["announce_webhook"], net_cfg["start_message"])
    log.info("Start announcement sent to Discord")

    if cfg["meshcore"]["enabled"]:
        post_meshcore_message(
            cfg["corescope"]["base_url"],
            cfg["meshcore"]["channel"],
            net_cfg["start_message"],
        )

def end_net(state: NetState, cfg: dict):
    count = state.close_window()
    net_cfg = cfg["net"]
    discord_cfg = cfg["discord"]

    summary = net_cfg["end_message_template"].format(count=count)
    send_discord(discord_cfg["announce_webhook"], summary)
    log.info("End announcement sent to Discord: %s", summary)

    if cfg["meshcore"]["enabled"]:
        post_meshcore_message(
            cfg["corescope"]["base_url"],
            cfg["meshcore"]["channel"],
            summary,
        )

# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def process_messages(messages, state: NetState, cfg: dict, seen_packets: set):
    if not state.is_open():
        return

    net_cfg = cfg["net"]
    discord_cfg = cfg["discord"]
    pattern = re.compile(net_cfg["checkin_pattern"], re.IGNORECASE)
    require_match = net_cfg.get("require_pattern_match", False)

    for msg in messages:
        packet_hash = msg.get("packetHash")
        if not packet_hash or packet_hash in seen_packets:
            continue

        sender = msg.get("sender", "Unknown")
        text = (msg.get("text") or "").strip()

        if not text or text == "P":
            seen_packets.add(packet_hash)
            save_seen(packet_hash)
            continue

        if require_match and not pattern.match(text):
            seen_packets.add(packet_hash)
            save_seen(packet_hash)
            continue

        send_discord(discord_cfg["checkin_webhook"], text, username=sender)
        state.add_participant(packet_hash, sender)
        log.info("Check-in relayed: %s | %s", sender, text)

        seen_packets.add(packet_hash)
        save_seen(packet_hash)

    if len(seen_packets) > 1000:
        trim_seen_file()
        seen_packets.clear()
        seen_packets.update(load_seen())

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()

    corescope_cfg = cfg["corescope"]
    sched_cfg = cfg["schedule"]

    tz = pytz.timezone(sched_cfg["timezone"])
    state = NetState()

    seen_packets = load_seen()

    # Preload current messages so we don't replay on startup
    log.info("Preloading seen packets...")
    for msg in fetch_messages(corescope_cfg["base_url"], corescope_cfg["channel"]):
        h = msg.get("packetHash")
        if h:
            seen_packets.add(h)

    # If we started inside the window (e.g. after a restart), open immediately
    if within_window(cfg):
        state.resume_window()

    # Parse start/end times
    start_h, start_m = (int(x) for x in sched_cfg["start_time"].split(":"))
    end_h, end_m = (int(x) for x in sched_cfg["end_time"].split(":"))
    dow = sched_cfg["day_of_week"]

    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        start_net,
        trigger="cron",
        day_of_week=dow,
        hour=start_h,
        minute=start_m,
        args=[state, cfg, seen_packets],
        id="start_net",
        misfire_grace_time=300,
    )
    scheduler.add_job(
        end_net,
        trigger="cron",
        day_of_week=dow,
        hour=end_h,
        minute=end_m,
        args=[state, cfg],
        id="end_net",
        misfire_grace_time=300,
    )
    scheduler.start()
    log.info(
        "Scheduler running — net window: %s %s–%s (%s)",
        dow.upper(),
        sched_cfg["start_time"],
        sched_cfg["end_time"],
        sched_cfg["timezone"],
    )

    poll_interval = corescope_cfg.get("poll_interval_seconds", 5)
    log.info("Polling %s every %ds", corescope_cfg["channel"], poll_interval)

    try:
        while True:
            messages = fetch_messages(corescope_cfg["base_url"], corescope_cfg["channel"])
            process_messages(messages, state, cfg, seen_packets)
            time.sleep(poll_interval)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down")
        scheduler.shutdown()

if __name__ == "__main__":
    main()
