"""Builds the map payload. The renderer owns all glyphs and layout.

Three tiers, each extending the same MAP command:

  1  The Audit Trail    last 10 steps of your path
  2  The Extended Log   last 20 steps
  3  Full Disclosure    the entire current floor

At every tier, the rooms immediately beyond any room you have stood in are
drawn too, marked explored or unexplored. Walking into a junction should tell
you what the junction connects to, because standing in it does.

Path history is recorded from the first move regardless of whether you own an
item, so finding a map on Floor 4 retroactively shows where you have been.
"""

from . import events as ev
from . import stalker as stalker_mod

TIER_TRAIL = {1: 10, 2: 20, 3: 999}

# How many steps back get a number. Single digits, because a cell is one
# character wide; anything older is just "explored".
TRAIL_LABELS = 9


def tier(state) -> int:
    if state.flags.get("map_full"):
        return 3
    if state.flags.get("map_extended"):
        return 2
    if state.flags.get("map_trail"):
        return 1
    return 0


def build(state, content):
    level = tier(state)
    if level == 0:
        return None

    floor = content.floor(state.floor)
    rooms = floor["rooms"]
    visited = set(state.visited)
    stalkers = stalker_mod.positions(state)

    trail_len = TIER_TRAIL[level]
    trail = [entry[0] for entry in state.path_history][-(trail_len - 1):]
    # Later occurrences win, so a revisited room shows its most recent index.
    trail_index = {}
    for i, rid in enumerate(trail):
        trail_index[rid] = len(trail) - i

    if level >= 3:
        core = set(rooms)
    else:
        core = (set(trail) | {state.room}) & set(rooms)

    # Reveal what the rooms you have stood in connect to. You saw the exits.
    shown = set(core)
    for rid in core:
        for dest in rooms[rid].get("exits", {}).values():
            if dest in rooms:
                shown.add(dest)

    cells = []
    for rid in sorted(shown):
        room = rooms[rid]
        seen = rid in visited
        safe = room.get("kind") == "safe"
        # Where you can spend paperclips is worth as much as where you can
        # rest, and harder to remember: the shop is one room out of forty.
        shop = seen and any(e["type"] == "npc" and e.get("shop")
                            for e in room.get("contents", []))

        if rid == state.room:
            kind, label = "you", "@"
        elif rid in stalkers:
            kind, label = "stalker", "S"
        elif shop:
            kind, label = "shop", "$"
        elif safe and seen:
            # Where the break room is matters more than how long ago you left.
            kind, label = "safe", "+"
        elif trail_index.get(rid, TRAIL_LABELS + 1) <= TRAIL_LABELS:
            # Only the last nine steps are numbered. This used to clamp with
            # min(9, ...) instead, so every room further back than nine steps
            # was labelled "9" - on Full Disclosure, where the trail is the
            # whole floor, that meant a screen full of nines where the legend
            # promises "1-9 steps back" and "o explored".
            kind, label = "step", str(trail_index[rid])
        elif seen:
            kind, label = "room", "o"
        else:
            kind, label = "unknown", "?"

        exits = {}
        for direction, dest in room.get("exits", {}).items():
            if dest not in shown:
                continue
            # Only claim a link if you have stood at one end of it.
            exits[direction] = ("walked" if (rid in visited or dest in visited)
                                else "known")

        cells.append({
            "x": room["pos"][0], "y": room["pos"][1], "room": rid,
            "label": label, "kind": kind, "exits": exits,
            "name": content.t(room["name_key"]) if seen else "",
        })

    # The floor's own name first, then which map you are holding, because
    # "FLOOR 11 - Full Disclosure" read as though the floor were called
    # Full Disclosure.
    return ev.map_shown(cells, content.t("map.legend"), level, state.floor,
                        content.t(floor["name_key"]))


def unavailable_event(content):
    return ev.error(content.t("map.no_item"))
