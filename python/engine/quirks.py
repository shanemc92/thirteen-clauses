"""Floor quirks and random events.

A quirk is a standing rule that applies for a whole floor and changes how the
floor is played, not just how it reads. A random event is a one-off that fires
on room entry from a per-floor weighted pool.

Both are pure data. Adding a quirk to a floor is a JSON edit; the vocabulary
below is the whole set of levers the engine exposes.

Quirk fields (all optional):
    key                 theme key, narrated once on arrival
    encounter_bonus     added to the floor's encounter chance
    stalker_speed       added to every stalker's pursuit roll
    flee_dc             added to the DC of escaping
    hide_exits          exits are not listed until you LOOK
    entry_toll          currency taken per new room entered
    rest_limit          how many rooms may be rested in (default unlimited)
    crit_range          natural roll at or above this crits (default 20)
    no_ability_restore  resting does not restore ability uses
    art_glitch          chance per room of the palette flickering
    shifting            revisited rooms may describe themselves differently
    slow_text           prose arrives at this many characters per second
    effect              animation played once, on arrival
"""

from . import events as ev
from . import shop as shop_mod

EMPTY = {}


def of(content, state):
    return content.floor(state.floor).get("quirk", EMPTY)


def encounter_chance(content, state, base):
    return max(0.0, min(0.95, base + of(content, state).get("encounter_bonus", 0.0)))


def stalker_speed_bonus(content, state):
    return of(content, state).get("stalker_speed", 0)


def flee_dc_bonus(content, state):
    return of(content, state).get("flee_dc", 0)


def crit_range(content, state):
    return of(content, state).get("crit_range", 20)


def hides_exits(content, state):
    return bool(of(content, state).get("hide_exits"))


def restores_abilities(content, state):
    return not of(content, state).get("no_ability_restore")


def shifts(content, state):
    return bool(of(content, state).get("shifting"))


def maybe_shift(state, content, rng, room_id, out):
    """On a shifting floor, a room you have already seen may not match.

    Returns True if this visit should use the room's alternate description.
    The map is deliberately not updated: a wrong map is a better puzzle than
    no map, and the geometry itself never changes so you cannot get stranded.
    """
    if not shifts(content, state):
        return False
    if room_id not in state.visited:
        return False
    if not rng.chance(of(content, state).get("shift_chance", 0.45)):
        return False
    out.append(ev.plain(content.t("quirk.shifted", rng)))
    return True


def text_speed(content, state):
    return of(content, state).get("slow_text", 0)


def announce(state, content, rng, out):
    """Narrate the quirk once, the first time you arrive on the floor."""
    quirk = of(content, state)
    key = quirk.get("key")
    if not key:
        return
    flag = f"quirk_seen.{state.floor}"
    if state.flags.get(flag):
        return
    state.flags[flag] = True
    out.append(ev.block(content.t(key)))


def apply_entry_toll(state, content, rng, out):
    toll = of(content, state).get("entry_toll", 0)
    if not toll or state.currency <= 0:
        return
    taken = min(toll, state.currency)
    state.currency -= taken
    out.append(ev.currency_changed(-taken, state.currency, "toll",
                              shop_mod.currency_name(content, 2)))
    out.append(ev.plain(content.t("quirk.toll_taken", amount=taken)))


# ---------------------------------------------------------------- events
def maybe_event(state, content, rng, out):
    """Roll the floor's random event table on room entry."""
    floor = content.floor(state.floor)
    table = floor.get("random_events")
    if not table:
        return
    if not rng.chance(floor.get("event_chance", 0.14)):
        return
    entry = rng.weighted([(e["id"], e["weight"]) for e in table])
    spec = next((e for e in table if e["id"] == entry), None)
    if spec:
        _fire(state, content, rng, spec, out)


def _fire(state, content, rng, spec, out):
    text = content.t(spec["key"], rng)
    out.append(ev.plain(text))
    op = spec.get("op")

    if op == "currency":
        low, high = spec.get("range", [1, 5])
        shop_mod.award(state, content, rng, low, high, out, "found")

    elif op == "item":
        from . import loot as loot_mod
        loot_mod.give(state, content, rng, [spec["item"]], out)

    elif op == "heal":
        from . import dice
        _, amount = dice.roll(spec.get("dice", "1d6"), rng)
        before = state.player.hp
        state.player.hp = min(state.player.hp_max, state.player.hp + amount)
        if state.player.hp > before:
            out.append(ev.plain(content.t("quirk.event_heal",
                                          amount=state.player.hp - before)))

    elif op == "damage":
        from . import dice
        _, amount = dice.roll(spec.get("dice", "1d4"), rng)
        state.player.hp = max(1, state.player.hp - amount)
        state.stats.damage_taken += amount
        out.append(ev.plain(content.t("quirk.event_damage", amount=amount,
                                      hp=state.player.hp)))

    elif op == "stalker":
        from . import combat as combat_mod
        from . import stalker as stalker_mod
        enemy = combat_mod.spawn(content, rng, spec["monster"], index=900)
        stalker_mod.add_stalker(state, content, enemy, out)

    elif op == "status":
        state.player.statuses[spec["status"]] = spec.get("rounds", 3)
        out.append(ev.status_changed(state.player.name, spec["status"], True,
                                     spec.get("rounds", 3)))

    elif op == "effect":
        out.append(ev.effect(spec["effect"], seconds=spec.get("seconds", 6)))
    elif op == "flicker":
        # A hint of what happens on Floor 7: half a second of spectrum.
        out.append(ev.palette_changed("rainbow"))
        out.append(ev.palette_changed(state.palette))

    elif op == "flag":
        state.flags[spec["flag"]] = spec.get("value", True)
