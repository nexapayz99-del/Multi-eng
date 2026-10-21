import os
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)


def _get(key, default=None, required=False):
    v = os.getenv(key, default)
    if v is None or v == "":
        if required:
            raise RuntimeError(f"Missing required env: {key} (check {ENV_PATH})")
        return default
    return v.strip()


API_ID = int(_get("API_ID", required=True))
API_HASH = _get("API_HASH", required=True)
BOT_TOKEN = _get("BOT_TOKEN", required=True)
MONGO_URI = _get("MONGO_URI", "mongodb://localhost:27017")
OWNER_ID = int(_get("OWNER_ID", required=True))
SESSION_KEY = _get("SESSION_KEY", "").encode()

MAX_AUTO_CHANNELS = 15
VC_JOIN_DELAY = 1
VC_JOIN_PARALLEL = 3
AUTO_POLL_INTERVAL = 20
VC_POLL_INTERVAL = 15
SILENT_WAV = "silent.wav"
POSITIVE_REACTIONS = ["👍", "❤️", "🔥", "🥰", "👏", "😁", "🎉", "🤩", "🙏", "💯", "😍", "👌"]

# ═══════════════════════════════════════════════════════
# FSub permanently disabled
# ═══════════════════════════════════════════════════════
USE_FSUB = False
FSUB_CHANNEL_ID = 0
FSUB_CHANNEL_LINK = ""

if API_ID <= 0:
    raise RuntimeError("API_ID must be a positive integer")
if OWNER_ID <= 0:
    raise RuntimeError("OWNER_ID must be a positive integer")
if not API_HASH:
    raise RuntimeError("API_HASH is required")
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing or invalid; check .env")