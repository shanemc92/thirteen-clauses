"""Build a floor JSON from a compact spec.

    python3 tools/mkfloor.py 03
    python3 tools/mkfloor.py all

A spec lists rooms with grid positions and a flat list of links. Directions are
derived from the positions, both sides are written, and non-adjacent links are
rejected. That removes the entire class of bug where a floor is hand-written
with a one-way door or an exit that contradicts the map grid.

Specs live in tools/specs/NN.py and are the source of truth. The generated
content/floors/NN.json is committed too, so the game never needs this script at
runtime.
"""

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_DIR = os.path.join(ROOT, "tools", "specs")
OUT_DIR = os.path.join(ROOT, "content", "floors")

DIRECTION = {(0, -1): "north", (0, 1): "south", (1, 0): "east", (-1, 0): "west"}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def load_spec(tag):
    path = os.path.join(SPEC_DIR, f"{tag}.py")
    spec = importlib.util.spec_from_file_location(f"floorspec_{tag}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SPEC


def build(spec):
    fid = spec["id"]
    prefix = f"{fid:02d}-"
    rooms = {}

    for short, meta in spec["rooms"].items():
        room = dict(meta)
        pos = room.pop("pos")
        room["pos"] = list(pos)
        room.setdefault("kind", "normal")
        room["name_key"] = f"rooms{fid}.{short}.name"
        room["desc_key"] = f"rooms{fid}.{short}.desc"
        room["long_desc_key"] = f"rooms{fid}.{short}.long"
        room["exits"] = {}
        rooms[prefix + short] = room

    positions = {rid: tuple(room["pos"]) for rid, room in rooms.items()}

    for a_short, b_short in spec["links"]:
        a, b = prefix + a_short, prefix + b_short
        for rid in (a, b):
            if rid not in rooms:
                raise SystemExit(f"floor {fid}: link references unknown room {rid}")
        ax, ay = positions[a]
        bx, by = positions[b]
        delta = (bx - ax, by - ay)
        if delta not in DIRECTION:
            raise SystemExit(
                f"floor {fid}: {a_short} {positions[a]} and {b_short} "
                f"{positions[b]} are not adjacent on the grid")
        direction = DIRECTION[delta]
        if direction in rooms[a]["exits"]:
            raise SystemExit(f"floor {fid}: {a_short} already has a {direction} exit")
        rooms[a]["exits"][direction] = b
        rooms[b]["exits"][OPPOSITE[direction]] = a

    # Reachability from the start.
    start = prefix + spec["start"]
    seen, stack = set(), [start]
    while stack:
        rid = stack.pop()
        if rid in seen:
            continue
        seen.add(rid)
        stack.extend(rooms[rid]["exits"].values())
    orphans = sorted(set(rooms) - seen)
    if orphans:
        raise SystemExit(f"floor {fid}: unreachable rooms {orphans}")

    floor = {
        "id": fid,
        "name_key": f"floors.{fid:02d}.name",
        "clause_key": f"floors.{fid:02d}.clause",
        "theme": spec["theme"],
        "palette": spec.get("palette", "mono"),
        "start": start,
        "boss_room": prefix + spec["boss_room"],
        "encounter_chance": spec["encounter_chance"],
        "clear_art": spec.get("clear_art", "floor_clear"),
        "last": spec.get("last", False),
        "encounter_table": spec["encounter_table"],
        "rooms": rooms,
    }
    for key in ("grants_continue", "grants_continue_on_death"):
        if spec.get(key):
            floor[key] = True
    return floor


def write(tag):
    floor = build(load_spec(tag))
    path = os.path.join(OUT_DIR, f"{tag}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(floor, handle, indent=2)
        handle.write("\n")
    print(f"wrote {path}  ({len(floor['rooms'])} rooms)")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "all":
        tags = sorted(f[:-3] for f in os.listdir(SPEC_DIR)
                      if f.endswith(".py") and not f.startswith("_"))
    else:
        tags = args
    for tag in tags:
        write(tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
