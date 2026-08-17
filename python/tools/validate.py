"""Validate content: schema, reachability, geometry, missing string keys.

    python3 tools/validate.py

Exit code 1 on any error, so this drops straight into CI.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.content import load_from_disk  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
DELTA = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def check_key(content, key, where):
    if key and content.raw(key) is None:
        err(f"{where}: missing theme key '{key}'")


def check_floor(content, floor):
    fid = floor["id"]
    rooms = floor["rooms"]
    check_key(content, floor["name_key"], f"floor {fid}")

    if floor["start"] not in rooms:
        err(f"floor {fid}: start room '{floor['start']}' does not exist")
    if floor.get("boss_room") and floor["boss_room"] not in rooms:
        err(f"floor {fid}: boss_room '{floor['boss_room']}' does not exist")

    for rid, room in rooms.items():
        where = f"floor {fid} room {rid}"
        for key in ("name_key", "desc_key"):
            check_key(content, room.get(key), where)
        check_key(content, room.get("long_desc_key"), where)
        if "pos" not in room:
            err(f"{where}: no pos, map cannot draw it")
        if room.get("art") and not content.get_art(room["art"]):
            warn(f"{where}: art '{room['art']}' not found")

        for direction, dest in room.get("exits", {}).items():
            if dest not in rooms:
                err(f"{where}: exit {direction} -> '{dest}' does not exist")
                continue
            back = rooms[dest].get("exits", {}).get(OPPOSITE.get(direction, ""))
            if back != rid:
                err(f"{where}: exit {direction} -> {dest} is not bidirectional")
            if direction in DELTA and "pos" in room and "pos" in rooms[dest]:
                dx, dy = DELTA[direction]
                want = [room["pos"][0] + dx, room["pos"][1] + dy]
                if list(rooms[dest]["pos"]) != want:
                    err(f"{where}: exit {direction} -> {dest} contradicts "
                        f"grid position {rooms[dest]['pos']} (expected {want})")

        for entry in room.get("contents", []):
            if entry["type"] == "chest":
                if entry["loot_table"] not in content.loot:
                    err(f"{where}: unknown loot table '{entry['loot_table']}'")
                if not entry.get("flag"):
                    err(f"{where}: chest has no flag, it will refill forever")
                check_key(content, entry.get("hint_key"), where)
                check_key(content, entry.get("open_key"), where)
            elif entry["type"] == "item":
                if entry["item"] not in content.items:
                    err(f"{where}: unknown item '{entry['item']}'")
            elif entry["type"] == "stash":
                # A stash that announces itself is not a stash, and one with
                # no flag pays out forever.
                if not entry.get("flag"):
                    err(f"{where}: stash has no flag")
                if entry.get("hint_key"):
                    err(f"{where}: a stash must not have a hint")
                if entry.get("low", 0) > entry.get("high", 0):
                    err(f"{where}: stash range is backwards")
            elif entry["type"] == "note":
                # A note with no flag is re-readable forever and files a
                # duplicate in the record every time.
                if not entry.get("flag"):
                    err(f"{where}: note has no flag")
                check_key(content, entry.get("text_key"), where)
                check_key(content, entry.get("hint_key"), where)
            elif entry["type"] == "npc":
                if content.raw(f"npcs.{entry['id']}") is None:
                    err(f"{where}: no npc definition for '{entry['id']}'")
                game = entry.get("minigame")
                if game:
                    from engine import minigames
                    if game not in minigames.REGISTRY:
                        err(f"{where}: unknown minigame '{game}'")
                    else:
                        for k in ("victory", "defeat", "penalty"):
                            check_key(content, f"minigame.{game}.{k}", where)
                config = entry.get("config", {})
                reward = config.get("reward")
                if reward and reward not in content.items:
                    err(f"{where}: unknown minigame reward '{reward}'")
                if game:
                    check_key(content, config.get("name_key"), where)
                if entry.get("shop"):
                    table = config.get("stock_table")
                    if table not in content.loot:
                        err(f"{where}: unknown stock table '{table}'")
                    check_key(content, entry.get("again_key"), where)
                    if config.get("machine"):
                        # A machine sells healing and nothing else; anything
                        # in the table that does not heal is unreachable
                        # comedy at best and a dead slot at worst.
                        rows = content.loot[table]
                        picks = (list(rows.get("always", []))
                                 + [e["item"] for e in rows.get("entries", [])])
                        for iid in picks:
                            if iid not in content.items:
                                err(f"{where}: unknown vending item '{iid}'")
                            elif content.items[iid].get("use", {}).get("op") \
                                    not in ("heal", "heal_full"):
                                err(f"{where}: vending machine stocks "
                                    f"non-healing '{iid}'")

        for mid in (room.get("boss"), room.get("elite"), room.get("miniboss")):
            if mid and mid not in content.monsters:
                err(f"{where}: unknown monster '{mid}'")

        for script in room.get("on_enter", []):
            if script["event"] == "narrator":
                if content.raw(f"narrator.voices.default.{script['key']}") is None:
                    err(f"{where}: narrator key '{script['key']}' missing "
                        f"from the default voice")
            elif script["event"] == "text":
                check_key(content, script["key"], where)
            elif script["event"] == "teleport":
                # A trapdoor re-enters enter_room. Two rules keep that from
                # looping or stranding content: it must not land on another
                # trapdoor, and the room it fires in can hold nothing,
                # because you are never in it long enough to reach anything.
                dest = script.get("to")
                if dest not in rooms:
                    err(f"{where}: teleport to unknown room '{dest}'")
                elif dest == rid:
                    err(f"{where}: teleport leads to itself")
                elif any(sc.get("event") == "teleport"
                         for sc in rooms[dest].get("on_enter", [])):
                    err(f"{where}: teleport lands on another teleport")
                if room.get("contents"):
                    err(f"{where}: teleport room has contents nobody can reach")
                check_key(content, script.get("key"), where)

    # Reachability
    seen, stack = set(), [floor["start"]]
    while stack:
        rid = stack.pop()
        if rid in seen or rid not in rooms:
            continue
        seen.add(rid)
        stack.extend(rooms[rid].get("exits", {}).values())
    for rid in rooms:
        if rid not in seen:
            err(f"floor {fid}: room {rid} is unreachable from the start")

    # Random-event strings were never checked, so twelve missing keys shipped
    # and printed as <<events.f12.ash>> on screen.
    for event in floor.get("random_events", []):
        check_key(content, event.get("key"), f"floor {fid} event {event['id']}")
        if event.get("op") == "item" and event.get("item") not in content.items:
            err(f"floor {fid} event {event['id']}: unknown item "
                f"{event.get('item')!r}")
        if event.get("op") == "stalker" and event.get("monster") not in content.monsters:
            err(f"floor {fid} event {event['id']}: unknown monster "
                f"{event.get('monster')!r}")

    from engine.step import floor_effects
    raised = floor_effects(floor)
    if raised:
        from frontends.terminal.effects import Effects
        for name in raised:
            if not hasattr(Effects, name):
                err(f"floor {fid}: unknown effect {name!r}")
        if len(set(raised)) != len(raised):
            err(f"floor {fid}: the same effect is raised twice: {raised}")

    quirk = floor.get("quirk", {})
    check_key(content, quirk.get("key"), f"floor {fid} quirk")

    for entry in floor.get("encounter_table", []):
        if entry["monster"] not in content.monsters:
            err(f"floor {fid}: encounter table references unknown "
                f"monster '{entry['monster']}'")


def check_floor_local_drops(content):
    """A random drop may only hand out items written for that floor.

    found_key text describes where a thing was found - a doorstep, a first
    aid bracket, a trough the crews could reach - so an item that drops
    somewhere its line is not true reads as a bug, because it is one. A
    casserole on Floor 6 was talking about a doorstep Floor 6 does not have.

    Buying is not finding: no found line is printed, so stock tables and
    vending machines are free to carry whatever the seller could plausibly
    have. Only chest tables are checked.
    """
    import re
    from collections import defaultdict

    # An item's home floor is the lowest floor whose common, good and hoard
    # tables all carry it: the floor it was written for.
    tiers = defaultdict(lambda: defaultdict(set))
    for name, table in content.loot.items():
        m = re.match(r"chest_f(\d+)_(common|good|hoard)$", name)
        if not m:
            continue
        for iid in (list(table.get("always", []))
                    + [e["item"] for e in table.get("entries", [])]):
            tiers[iid][int(m.group(1))].add(m.group(2))

    declared = {}
    for iid, floors in tiers.items():
        full = sorted(n for n, seen in floors.items() if len(seen) == 3)
        if full:
            declared[iid] = full[0]

    for name, table in content.loot.items():
        m = re.match(r"chest_f(\d+)(?:_(?:common|good|hoard|medical))?$", name)
        if not m:
            continue
        n = int(m.group(1))
        medical = name.endswith("_medical")
        for iid in (list(table.get("always", []))
                    + [e["item"] for e in table.get("entries", [])]):
            if iid not in content.items:
                err(f"loot {name}: unknown item '{iid}'")
                continue
            spec = content.items[iid]
            if not spec.get("found_key"):
                continue        # no line, so nothing to contradict
            home = spec.get("found_floor")
            if home is None:
                if declared.get(iid) not in (None, n):
                    err(f"loot {name}: '{iid}' drops on floor {n} but its "
                        f"tables place it on {declared[iid]}; give it a "
                        f"found_floor if the line travels")
                continue
            # A medical chest also carries what somebody brought down from
            # the two floors above; loot.found_line swaps in the carried-down
            # line off the item's own floor, so the text stays true.
            lowest = home if not medical else home
            highest = home if not medical else home + 2
            if not lowest <= n <= highest:
                err(f"loot {name}: '{iid}' drops on floor {n}, but its found "
                    f"line is written for floor {home}"
                    + (" (chests reach two floors down at most)" if medical else ""))

    for name, table in content.loot.items():
        if not re.match(r"(?:chest_f\d+_medical|vending_f\d+)$", name):
            continue
        for iid in (list(table.get("always", []))
                    + [e["item"] for e in table.get("entries", [])]):
            spec = content.items.get(iid)
            if spec is None:
                err(f"loot {name}: unknown item '{iid}'")
                continue
            use = spec.get("use") or {}
            if use.get("op") not in ("heal", "heal_full"):
                err(f"loot {name}: '{iid}' does not heal")
            if use.get("grants_flag") or spec.get("price", 0) > 300:
                err(f"loot {name}: '{iid}' is a one-off prize, not stock")


def check_registry(content):
    for cid, cls in content.classes.items():
        for key in ("name_key", "desc_key", "verb_key"):
            check_key(content, cls[key], f"class {cid}")
        if not content.get_art(cls.get("art", "")):
            warn(f"class {cid}: art missing")
        for grant in cls["abilities"]:
            if grant["id"] not in content.abilities:
                err(f"class {cid}: unknown ability '{grant['id']}'")
        for iid in cls.get("starting_items", []):
            if iid not in content.items:
                err(f"class {cid}: unknown starting item '{iid}'")
        for iid in cls.get("starting_equipment", {}).values():
            if iid not in content.items:
                err(f"class {cid}: unknown starting equipment '{iid}'")

    for cid, comp in content.companions.items():
        for key in ("name_key", "desc_key", "passive_key", "stabilise_key"):
            check_key(content, comp[key], f"companion {cid}")
        if not content.get_art(comp.get("art", "")):
            warn(f"companion {cid}: art missing")
        # _inspect_chest in step.py builds these at runtime, one per outcome
        # of _chest_quirk, so nothing else here would ever look them up.
        # chest_mimic shipped missing and only showed up when a companion
        # who inspects chests met a mimic.
        if comp.get("inspects_chests"):
            for quirk in ("clear", "jammed", "rigged", "mimic"):
                check_key(content, f"companions.{cid}.chest_{quirk}",
                          f"companion {cid}")

    for aid, ab in content.abilities.items():
        for key in ("name_key", "desc_key", "flavour_key"):
            check_key(content, ab.get(key), f"ability {aid}")

    for iid, item in content.items.items():
        for key in ("name_key", "desc_key"):
            check_key(content, item[key], f"item {iid}")
        check_key(content, item.get("found_key"), f"item {iid}")

    for mid, mon in content.monsters.items():
        check_key(content, mon["name_key"], f"monster {mid}")
        for atk in mon["attacks"]:
            check_key(content, atk["name_key"], f"monster {mid}")
        for taunt in mon.get("taunts", []):
            check_key(content, taunt, f"monster {mid}")
        if mon.get("loot_table") and mon["loot_table"] not in content.loot:
            err(f"monster {mid}: unknown loot table")
        if not content.get_art(mon.get("art", "")):
            warn(f"monster {mid}: art missing")

    for tid, table in content.loot.items():
        for iid in table.get("always", []):
            if iid not in content.items:
                err(f"loot {tid}: unknown item '{iid}'")
        for entry in table.get("entries", []):
            if entry["item"] != "nothing" and entry["item"] not in content.items:
                err(f"loot {tid}: unknown item '{entry['item']}'")


ART_BUDGET = 44   # fits a phone terminal in landscape with room for the indent


def check_art(content):
    for key, art in content.art.items():
        lines = art.split("\n")
        widths = {len(line) for line in lines}
        block = max(widths, default=0)
        if block > ART_BUDGET:
            err(f"art {key}: {block} columns, budget is {ART_BUDGET}. "
                f"It will wrap on small terminals.")
        if len(widths) > 1:
            err(f"art {key}: not padded to a rectangle "
                f"({len(widths)} distinct line widths)")
        for line in lines:
            if any(ord(ch) > 126 for ch in line):
                err(f"art {key}: non-ASCII character")
                break


def main():
    content = load_from_disk(os.path.join(ROOT, "content"))
    check_art(content)
    check_registry(content)
    check_floor_local_drops(content)
    for floor in content.floors.values():
        check_floor(content, floor)

    for msg in warnings:
        print(f"WARN  {msg}")
    for msg in errors:
        print(f"ERROR {msg}")
    print(f"\ncontent version {content.version}: "
          f"{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
