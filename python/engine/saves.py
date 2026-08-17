"""Save format: gzipped JSON, base64'd, wrapped in a text envelope.

Text so it survives copy-paste, email, and a browser Blob download equally.
The engine only deals in strings; the frontend decides where bytes go.
"""

import base64
import gzip
import json
import time

MAGIC = "13SAVE"
HEADER = f"----- {MAGIC} v1 -----"
FOOTER = f"----- END {MAGIC} -----"


def encode(state, saved_at=None) -> str:
    """Stamp the save with its own write time.

    File mtime is unreliable: several Android filesystems round it to the
    nearest second or two, so saves written close together tie and "most
    recent" silently falls back to directory order.
    """
    data = state.to_dict()
    data["saved_at"] = time.time() if saved_at is None else float(saved_at)
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    blob = base64.b64encode(gzip.compress(payload, 9)).decode("ascii")
    lines = [blob[i:i + 76] for i in range(0, len(blob), 76)]
    return "\n".join([HEADER, *lines, FOOTER, ""])


def decode(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("-----")]
    blob = "".join(lines)
    payload = gzip.decompress(base64.b64decode(blob))
    return json.loads(payload)


class SaveError(Exception):
    pass


def written_at(text: str, fallback: float = 0.0) -> float:
    """The save's own timestamp, or the fallback if it predates stamping."""
    try:
        return float(decode(text).get("saved_at") or fallback)
    except Exception:
        return fallback


def load_state(text: str, content, allow_dead: bool = False):
    from .state import GameState, SCHEMA_VERSION
    try:
        data = decode(text)
    except Exception as exc:
        raise SaveError(f"unreadable save file: {exc}") from exc

    if data.get("schema_version") != SCHEMA_VERSION:
        raise SaveError(
            f"save is schema v{data.get('schema_version')}, "
            f"this build reads v{SCHEMA_VERSION}")

    warning = None
    if data.get("content_version") != content.version:
        warning = ("Content has changed since this save was written. "
                   "Rooms and items may not line up.")

    # Rebuilding the state is as much a parse step as decoding was, and it is
    # the one that touches user-editable structure. Anything it throws has to
    # arrive as a SaveError or the caller's `except SaveError` misses it and
    # a hand-edited file takes the whole session down.
    try:
        state = GameState.from_dict(data)
    except SaveError:
        raise
    except Exception as exc:
        raise SaveError(f"save file is damaged: {exc}") from exc

    if not isinstance(state.flags, dict):
        raise SaveError("save file is damaged: flags are not a mapping")
    if state.flags.get("dead") and not allow_dead:
        raise SaveError("dead")
    return state, warning
