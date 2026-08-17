"""Monsters that follow you between rooms and pounce.

`distance` means rooms behind you, which is a direct index into
state.path_history. That is what lets the map draw stalkers for free.
"""

from . import events as ev
from .state import Stalker

MAX_STALKERS = 3


def add_stalker(state, content, enemy, out):
    if len(state.stalkers) >= MAX_STALKERS:
        return
    mon = content.monster(enemy.monster_id)
    spec = mon.get("stalker", {})
    stalk = Stalker(
        monster_id=enemy.monster_id,
        name=enemy.name,
        distance=spec.get("start_distance", 2),
        patience=spec.get("patience", 6),
        hp_carried=max(1, enemy.hp),
    )
    state.stalkers.append(stalk)
    out.append(ev.plain(content.t("stalker.acquired", name=enemy.name)))


def clear_stalkers(state, content, out, reason="safe"):
    if not state.stalkers:
        return
    for stalk in state.stalkers:
        out.append(ev.stalker_lost(stalk.monster_id, stalk.name))
        out.append(ev.plain(content.t(f"stalker.lost_{reason}", name=stalk.name)))
        state.flags[f"wounded.{stalk.monster_id}"] = stalk.hp_carried
    state.stalkers = []


def tick(state, content, rng):
    """Called on every room transition. Returns (events, pounce_or_None)."""
    out = []
    pounce = None
    survivors = []

    for stalk in state.stalkers:
        stalk.patience -= 1
        if stalk.patience <= 0:
            out.append(ev.stalker_lost(stalk.monster_id, stalk.name))
            out.append(ev.plain(content.t("stalker.gave_up", rng, name=stalk.name)))
            state.flags[f"wounded.{stalk.monster_id}"] = stalk.hp_carried
            continue

        mon = content.monster(stalk.monster_id)
        from . import quirks
        speed = (mon.get("stalker", {}).get("speed", 1)
                 + quirks.stalker_speed_bonus(content, state))
        roll = rng.d(20) + speed
        dc = 10 + state.player.mod("dex")
        if roll >= dc:
            stalk.distance -= 1
            if stalk.distance <= 0:
                pounce = stalk
                out.append(ev.pounced(stalk.monster_id, stalk.name))
                continue
            out.append(ev.stalker_closer(stalk.monster_id, stalk.distance, stalk.name))
            out.append(ev.plain(content.t("stalker.closer", rng, name=stalk.name)))
        else:
            out.append(ev.plain(content.t("stalker.behind", rng, name=stalk.name)))
        survivors.append(stalk)

    state.stalkers = survivors
    return out, pounce


def positions(state) -> dict:
    """room_id -> stalker name, for the map renderer."""
    result = {}
    history = state.path_history
    for stalk in state.stalkers:
        idx = len(history) - stalk.distance
        if 0 <= idx < len(history):
            result[history[idx][0]] = stalk.name
    return result
