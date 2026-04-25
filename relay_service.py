import hashlib
import logging
import os
import secrets

from fastapi import FastAPI, Header, HTTPException
from meshcore import MeshCore, EventType
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RELAY_TOKEN = os.environ["RELAY_TOKEN"]
COMPANION_HOST = os.environ["COMPANION_HOST"]
COMPANION_PORT = int(os.environ.get("COMPANION_PORT", "4403"))

_PUBLIC_KEY = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")

app = FastAPI()


def _channel_type(name: str) -> str:
    if name.lower() == "public":
        return "public"
    if name.startswith("#"):
        return "hash"
    return "private"


async def _resolve_channel(mc: MeshCore, name: str) -> int | None:
    """Return channel index for name, provisioning hash/public channels if needed.

    Returns None when the channel is private and not already on the companion.
    """
    ctype = _channel_type(name)

    channel_map: dict[str, int] = {}
    for idx in range(8):
        result = await mc.commands.get_channel(idx)
        if result.type == EventType.ERROR:
            continue
        slot_name = result.payload.get("name", "").strip()
        if result.payload.get("secret") == _PUBLIC_KEY:
            channel_map["public"] = idx
        elif slot_name:
            channel_map[slot_name] = idx

    map_key = "public" if ctype == "public" else name
    if map_key in channel_map:
        return channel_map[map_key]

    if ctype == "private":
        return None

    used = set(channel_map.values())
    free_idx = next((i for i in range(8) if i not in used), None)
    if free_idx is None:
        raise RuntimeError("No free channel slots (all 8 occupied)")

    if ctype == "public":
        device_name, secret = "PUBLIC", _PUBLIC_KEY
    else:
        device_name = name
        secret = hashlib.sha256(name.encode()).digest()[:16]

    result = await mc.commands.set_channel(free_idx, device_name, secret)
    if result.type == EventType.ERROR:
        raise RuntimeError(f"set_channel failed: {result.payload}")

    log.info("Provisioned '%s' (type=%s) at index %d", name, ctype, free_idx)
    return free_idx


class SendRequest(BaseModel):
    text: str
    group: str


@app.post("/send")
async def send(body: SendRequest, authorization: str = Header(...)):
    if not secrets.compare_digest(authorization, f"Bearer {RELAY_TOKEN}"):
        raise HTTPException(status_code=401)

    mc = await MeshCore.create_tcp(COMPANION_HOST, COMPANION_PORT)
    try:
        try:
            channel_idx = await _resolve_channel(mc, body.group)
        except RuntimeError as e:
            log.error("Channel error: %s", e)
            raise HTTPException(status_code=502, detail=str(e))

        if channel_idx is None:
            raise HTTPException(status_code=404, detail=f"Private group '{body.group}' not configured on companion")

        result = await mc.commands.send_chan_msg(channel_idx, body.text)
        if result.type == EventType.ERROR:
            log.warning("send_chan_msg error: %s", result.payload)
            raise HTTPException(status_code=502, detail="Companion error")
    finally:
        await mc.disconnect()

    log.info("Sent to '%s' (idx %d): %s", body.group, channel_idx, body.text)
    return {"ok": True}
