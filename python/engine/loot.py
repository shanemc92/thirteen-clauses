"""Loot tables. Weighted, with an optional guaranteed entry."""

from . import events as ev


def roll_table(content, rng, table_id):
    table = content.loot.get(table_id)
    if not table:
        return []
    picked = list(table.get("always", []))
    draws = table.get("draws", 1)
    entries = [(e["item"], e["weight"]) for e in table.get("entries", [])]
    for _ in range(draws):
        item = rng.weighted(entries)
        if item and item != "nothing":
            picked.append(item)
    return picked


def found_line(state, content, rng, item):
    """What to say about where a thing was found.

    A located found line describes a place - a doorstep, a first aid bracket,
    a trough the crews could reach - and is only true on the floor it was
    written for. Chests carry items from up to two floors above as well, so
    off its own floor the item gets the carried-down line instead: somebody
    brought it with them and did not get any further.
    """
    key = item.get("found_key")
    if not key:
        return ""
    home = item.get("found_floor")
    if home is not None and home != state.floor:
        return content.t("loot.carried_down", rng)
    return content.t(key, rng)


def give(state, content, rng, item_ids, out, note="", stash_key=None):
    """Hand over items. Anything that will not fit is left in the room.

    Losing a drop to a full pack with no way back is the kind of thing that
    makes people stop playing, so it waits instead: drop something, TAKE again.
    """
    left_behind = []
    for iid in item_ids:
        item = content.item(iid)
        name = content.t(item["name_key"])

        if item.get("key_item"):
            if item.get("grants_flag"):
                state.flags[item["grants_flag"]] = True
            if state.add_keepsake(iid):
                out.append(ev.item_found(iid, name,
                                         found_line(state, content, rng, item)))
            else:
                out.append(ev.plain(content.t("loot.already_have", name=name)))
            continue

        if state.inventory_full():
            out.append(ev.plain(content.t("loot.full", name=name)))
            left_behind.append(iid)
            continue
        state.add_item(iid)
        out.append(ev.item_found(iid, name,
                                 note or found_line(state, content, rng, item)))
        if item.get("grants_flag"):
            state.flags[item["grants_flag"]] = True
    if stash_key is not None:
        if left_behind:
            state.flags[f"stash.{stash_key}"] = left_behind
        else:
            state.flags.pop(f"stash.{stash_key}", None)
    return left_behind
