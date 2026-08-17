"""Content loading.

The engine only ever touches the Content object, never the filesystem. The
terminal frontend builds one from disk; the browser frontend will build the
identical object from a fetched/bundled blob.
"""

import hashlib
import json
import os


class Content:
    def __init__(self, blob: dict):
        self.theme = blob["theme"]
        self.classes = blob["classes"]
        self.companions = blob["companions"]
        self.monsters = blob["monsters"]
        self.items = blob["items"]
        self.abilities = blob["abilities"]
        self.loot = blob["loot"]
        self.floors = {int(k): v for k, v in blob["floors"].items()}
        self.art = blob["art"]
        self.version = blob["version"]

    # -- strings ---------------------------------------------------------
    def raw(self, key: str, default=None):
        node = self.theme
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def t(self, key: str, rng=None, **args) -> str:
        """Resolve a string key. Lists pick one entry (needs rng for variety).

        An empty or absent key resolves to nothing: plenty of content fields
        are optional (`found_key`, `flavour_key`) and callers pass "" for
        them, which used to render as a literal <<>> on screen.
        """
        if not key:
            return ""
        value = self.raw(key)
        if value is None:
            return f"<<{key}>>"
        if isinstance(value, list):
            value = rng.choice(value) if rng else value[0]
        if args:
            try:
                value = value.format(**args)
            except (KeyError, IndexError):
                pass
        return value

    def voice(self, key: str, rng=None, **args):
        """A narrator line. There is one narrator; the id is gone."""
        value = self.raw(f"narrator.voices.default.{key}")
        if value is None:
            return None
        if isinstance(value, list):
            value = rng.choice(value) if rng else value[0]
        try:
            return value.format(**args) if args else value
        except (KeyError, IndexError):
            return value

    # -- lookups ---------------------------------------------------------
    def floor(self, n: int) -> dict:
        return self.floors[n]

    def room(self, floor_n: int, room_id: str) -> dict:
        return self.floors[floor_n]["rooms"][room_id]

    def monster(self, mid: str) -> dict:
        return self.monsters[mid]

    def item(self, iid: str) -> dict:
        return self.items[iid]

    def ability(self, aid: str) -> dict:
        return self.abilities[aid]

    def get_art(self, key: str) -> str:
        return self.art.get(key, "")


def _pad(raw: str) -> str:
    """Pad every line to the block width.

    Terminals that right-align, centre, or re-wrap ragged output make ASCII art
    look broken. A true rectangle renders the same everywhere.
    """
    lines = raw.rstrip("\n").split("\n")
    # Strip the common left margin so every art block starts flush left.
    # Some blocks were composed with an indent baked in and some were not,
    # which is what made the art look inconsistently placed.
    margins = [len(line) - len(line.lstrip(" "))
               for line in lines if line.strip()]
    cut = min(margins) if margins else 0
    lines = [line[cut:] if line.strip() else "" for line in lines]
    width = max((len(line) for line in lines), default=0)
    return "\n".join(line.ljust(width) for line in lines)


def load_from_disk(root: str) -> Content:
    root = os.path.abspath(root)
    hasher = hashlib.sha256()

    def read(path):
        with open(path, "r", encoding="utf-8") as handle:
            data = handle.read()
        hasher.update(data.encode("utf-8"))
        return data

    def read_json(name):
        return json.loads(read(os.path.join(root, name)))

    floors = {}
    floor_dir = os.path.join(root, "floors")
    for fname in sorted(os.listdir(floor_dir)):
        if fname.endswith(".json"):
            data = json.loads(read(os.path.join(floor_dir, fname)))
            floors[data["id"]] = data

    art = {}
    art_dir = os.path.join(root, "art")
    if os.path.isdir(art_dir):
        for fname in sorted(os.listdir(art_dir)):
            if fname.endswith(".txt"):
                art[fname[:-4]] = _pad(read(os.path.join(art_dir, fname)))

    blob = {
        "theme": read_json("theme.json"),
        "classes": read_json("classes.json"),
        "companions": read_json("companions.json"),
        "monsters": read_json("monsters.json"),
        "items": read_json("items.json"),
        "abilities": read_json("abilities.json"),
        "loot": read_json("loot.json"),
        "floors": floors,
        "art": art,
        "version": "",
    }
    blob["version"] = hasher.hexdigest()[:12]
    return Content(blob)


# ---------------------------------------------------------------- naming
SMALL_WORDS = {"a", "an", "and", "the", "of", "for", "from", "in", "on", "to",
               "with", "at", "by"}


def title_case(text):
    """Title Case that survives apostrophes and keeps small words lower.

    `str.title()` turns "the Greeter's badge" into "The Greeter'S Badge" and
    "RAID-6" into "Raid-6". Item names are stored in sentence case so they
    read correctly inside prose ("You ready the courier jacket."); anywhere
    a name is a label rather than a sentence - the record, the inventory,
    the shop, the sheet - it gets run through this instead.
    """
    words = str(text).split()
    out = []
    for i, word in enumerate(words):
        low = word.lower()
        if i and low in SMALL_WORDS:
            out.append(low)
        else:
            out.append(word[0].upper() + word[1:] if word else word)
    return " ".join(out)
