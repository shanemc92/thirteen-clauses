"""step(state, action, content) -> (state, events)

Single entry point. Mutates and returns the same state object; nothing else in
the engine is allowed to change state. No I/O, no printing, no blocking.
"""

from . import combat as combat_mod
from . import dice
from . import events as ev
from . import loot as loot_mod
from . import mapview
from . import minigames
from . import progression
from . import quirks
from . import shop as shop_mod
from . import stalker as stalker_mod
from .content import title_case
from .rng import Rng
from .state import (MODE_CHOICE, MODE_COMBAT, MODE_DEAD, MODE_EXPLORE,
                    MODE_MINIGAME, MODE_SHOP, MODE_WON)

DIRECTIONS = {"n": "north", "s": "south", "e": "east", "w": "west",
              "north": "north", "south": "south", "east": "east", "west": "west",
              "u": "up", "d": "down", "up": "up", "down": "down"}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east",
            "up": "down", "down": "up"}

# A floor's browser overlay (Floors 5, 8, 9, 10, 12, 13) used to hold for the
# whole floor. It now fires once as a burst, timed to this, the moment you
# leave the entrance room - long enough to land, short enough that it is
# gone well before it has outstayed its welcome on room three or four.
ENTRANCE_EFFECT_SECONDS = 30.0


def _rng(state) -> Rng:
    return Rng(state.seed, state.rng_counter)


def _sync(state, rng):
    state.rng_counter = rng.counter


def narrate_floor(state, content, rng, base, out, **args):
    """Narrate `base_<floor>` if there is a line for it, else plain `base`.

    Checking only the default voice meant an alternate track fell back to its
    own generic line, and those still had Floor 1 wording baked in.
    """
    variant = f"{base}_{state.floor}"
    if content.voice(variant, rng) is not None:
        narrate(state, content, rng, variant, out, **args)
    else:
        narrate(state, content, rng, base, out, **args)


def narrate(state, content, rng, key, out, **args):
    line = content.voice(key, rng, **args)
    if line:
        out.append(ev.narration(line))


# ------------------------------------------------------------------ rooms
def describe_room(state, content, rng, room_id, first_visit, out, brief=False,
                  shifted=False):
    floor = content.floor(state.floor)
    room = floor["rooms"][room_id]
    desc_key = room["desc_key"] if (brief or not first_visit) else room.get(
        "long_desc_key", room["desc_key"])
    if shifted and room.get("alt_desc_key"):
        desc_key = room["alt_desc_key"]
    exits = {}
    hide = quirks.hides_exits(content, state) and not brief
    for direction, dest in room.get("exits", {}).items():
        if hide and not state.flags.get(f"looked.{room_id}"):
            continue
        exits[direction] = dest in state.visited
    out.append(ev.room_entered(
        room_id=room_id,
        name=content.t(room["name_key"]),
        desc=content.t(desc_key, rng),
        exits=exits,
        first_visit=first_visit,
        kind=room.get("kind", "normal"),
    ))
    art_key = room.get("art")
    if art_key and first_visit:
        art = content.get_art(art_key)
        if art:
            out.append(ev.art_shown(art_key, art))

    for entry in room.get("contents", []):
        if entry["type"] == "chest" and not state.flags.get(entry["flag"]):
            out.append(ev.plain(content.t(entry.get("hint_key", "room.chest_hint"), rng)))
        elif entry["type"] == "npc":
            if not state.flags.get(f"talked.{entry['id']}"):
                out.append(ev.plain(content.t(entry.get("hint_key", "room.npc_hint"), rng)))
            elif entry.get("shop"):
                # The merchant is the one NPC you are meant to come back to,
                # and suppressing his hint after the first conversation hid
                # the only sign he was still standing there.
                out.append(ev.plain(content.t(
                    entry.get("again_key", "rooms_common.merchant_again"), rng)))
        elif entry["type"] == "note" and not state.flags.get(entry["flag"]):
            out.append(ev.plain(content.t(entry.get("hint_key", "room.note_hint"), rng)))
        elif entry["type"] == "item" and not state.flags.get(entry["flag"]):
            out.append(ev.plain(content.t(entry.get("hint_key", "room.item_hint"), rng)))

    # Every safe room has the photograph in it. Say so, or nobody ever looks.
    if room.get("kind") == "safe":
        seen = set(state.flags.get("photo_floors", []))
        if state.floor in seen:
            key = "rooms_common.photo_same_floor"
        elif seen:
            key = "rooms_common.photo_here_again"
        else:
            key = "rooms_common.photo_here"
        out.append(ev.plain(content.t(key, rng)))


def floor_effects(floor):
    """The effects a floor raises, as a list.

    A floor may name one effect or several: Floor 12 runs `storm` and `ember`
    together, because its own random events are about ash coming down as well
    as the weather, and one overlay could only say half of that. The JSON
    still accepts a plain string.
    """
    raised = floor.get("effect")
    if not raised:
        return []
    return [raised] if isinstance(raised, str) else list(raised)


def floor_effect_seconds(floor):
    """How long this floor's entrance effect runs.

    Defaults to ENTRANCE_EFFECT_SECONDS. A floor may shorten or lengthen it
    with `effect_seconds`, because the overlays are not equally kind to look
    at: Floor 13's `blank` drains the whole page toward white, and thirty
    seconds of that is a long time to read through.
    """
    try:
        seconds = float(floor.get("effect_seconds", ENTRANCE_EFFECT_SECONDS))
    except (TypeError, ValueError):
        return ENTRANCE_EFFECT_SECONDS
    # Bounded: the wrapper treats anything outside 0-3600 as "use the default"
    # (see fire() in effects.js), so silently agree with it here.
    if not 0 < seconds < 3600:
        return ENTRANCE_EFFECT_SECONDS
    return seconds


def _diecut(state, content, rng, out):
    """The Floor 6 die-cutter, in the Licensing Office.

    Holding the laminated notice, the machine gives it an edge and it becomes
    a weapon — Floor 7 tier, which is where you are about to be. Not holding
    one, it is a machine set up to punch toy swords out of flat card, which
    is the joke and, for anyone who has already sold their laminate for two
    paperclips, the clue for the next run.

    Fires every time the room is entered. Only the cutting is once-only
    (`laminate_cut`); the idle comment is a fixture of the room.
    """
    from . import loot as loot_mod
    if any(e["id"] == "laminate" for e in state.inventory):
        if state.flags.get("laminate_cut"):
            return
        state.flags["laminate_cut"] = True
        state.remove_item("laminate")
        out.append(ev.plain(content.t("secret.diecut_cut")))
        loot_mod.give(state, content, rng, ["cut_laminate"], out)
        return
    out.append(ev.plain(content.t("secret.diecut_idle", rng)))


def boss_threshold_rooms(floor):
    """Rooms with a door straight into the boss room.

    The floor effect is raised again here so the last stretch before a boss
    is set the same way the arrival was, without holding the overlay for the
    whole floor.
    """
    boss = floor.get("boss_room")
    if not boss:
        return set()
    return {rid for rid, room in floor["rooms"].items()
            if boss in room.get("exits", {}).values()}


def _effect_should_fire(state, floor, room_id, direction):
    """A floor effect is a burst at three moments and nowhere else.

    Arrival (a new game, a descent, or a load - see resume), and the first
    time the room outside the boss door is entered. Both run for
    ENTRANCE_EFFECT_SECONDS; the point is to set the scene twice, not to
    follow the player around the floor.
    """
    key = f"effect_threshold.{state.floor}"
    if not direction:
        state.flags.pop(key, None)
        return True
    if room_id not in boss_threshold_rooms(floor) or state.flags.get(key):
        return False
    state.flags[key] = True
    return True


def enter_room(state, content, rng, room_id, direction, out):
    floor = content.floor(state.floor)
    room = floor["rooms"][room_id]
    first_visit = room_id not in state.visited

    if direction:
        state.push_path(state.room, direction)
    state.room = room_id
    if first_visit:
        state.visited.append(room_id)
    state.stats.rooms_entered += 1

    # Text pacing is per floor: Clause 10 does everything at the speed of
    # procedure, which is slowly.
    speed = quirks.text_speed(content, state)
    if speed != state.flags.get("text_speed", 0):
        state.flags["text_speed"] = speed
        out.append(ev.text_speed(speed))

    # Palette flip is data-driven per floor. Floor 7's rainbow is only the
    # entrance room's beat - every room after it reverts to normal, rather
    # than painting the whole floor.
    want = floor.get("palette", "mono")
    if want == "rainbow" and direction:
        want = "full"
    if want != state.palette:
        state.palette = want
        # Floor 7's spectrum is a terminal joke; Floor 8 does the browser
        # version, so the overlay is not spent a floor early.
        out.append(ev.palette_changed(want, notify=(want != "rainbow")))
        if want not in ("mono", "none"):
            narrate(state, content, rng, "palette_break", out)

    # Stalkers move before the room is described. Dread first, prose second.
    pounce = None
    if direction:
        stalk_events, pounce = stalker_mod.tick(state, content, rng)
        out.extend(stalk_events)

    shifted = quirks.maybe_shift(state, content, rng, room_id, out)
    if shifted:
        out.append(ev.effect("amend", seconds=1.6))
    describe_room(state, content, rng, room_id, first_visit, out,
                  shifted=shifted)

    for script in room.get("on_enter", []):
        if script.get("once") and state.flags.get(script.get("flag", "")):
            continue
        if script.get("flag"):
            state.flags[script["flag"]] = True
        if script["event"] == "narrator":
            narrate(state, content, rng, script["key"], out)
        elif script["event"] == "text":
            out.append(ev.plain(content.t(script["key"], rng)))
        elif script["event"] == "flag":
            state.flags[script["set"]] = script.get("value", True)
        elif script["event"] == "diecut":
            _diecut(state, content, rng, out)
        elif script["event"] == "teleport":
            # A trapdoor. Floor 9 is severed into pieces that do not obviously
            # join up, so a one-way drop is the floor's own logic rather than
            # a punishment: no damage, no encounter, you are simply somewhere
            # else. Recursion is not a risk because enter_room is re-entered
            # with a fresh direction and the destination has no teleport of
            # its own; the validator enforces that.
            dest = script["to"]
            if dest in floor["rooms"] and dest != room_id:
                if script.get("key"):
                    out.append(ev.plain(content.t(script["key"], rng)))
                out.append(ev.pause())
                state.stats.trapdoors_fallen += 1
                enter_room(state, content, rng, dest, script.get("as", "down"), out)
                return

    _companion_recover(state, content, out)
    raised = floor_effects(floor)
    if raised and _effect_should_fire(state, floor, room_id, direction):
        for name in raised:
            out.append(ev.effect(name, seconds=floor_effect_seconds(floor)))

    quirks.announce(state, content, rng, out)
    if first_visit:
        quirks.apply_entry_toll(state, content, rng, out)
    quirks.maybe_event(state, content, rng, out)
    _voices_ambient(state, content, rng, out)

    if room.get("kind") == "safe":
        stalker_mod.clear_stalkers(state, content, out, reason="safe")
        out.append(ev.plain(content.t("room.safe_available")))

    if pounce is not None:
        state.stats.pounces_survived += 1
        out.append(ev.plain(content.t("stalker.pounce", name=pounce.name)))
        # Grunk takes the first ambush on each floor so you are not surprised.
        surprised = not _absorbs_pounce(state, content, out)
        out.extend(combat_mod.start_combat(
            state, content, rng, [pounce.monster_id], surprised=surprised,
            carried_hp=pounce.hp_carried, source_room=room_id))
        return

    # The last room is a conversation. It is the only one in the building.
    if room.get("finale") and not state.flags.get("finale_done"):
        _finale_open(state, content, rng, out)
        return

    # A finale fight you walked out of is still waiting. The room is the end
    # of the game, so there is nothing else in it to do: without this you
    # could flee, come back, and stand in an empty white room forever with
    # no way to finish the run.
    unfinished = state.flags.get("finale_fight")
    if room.get("finale") and unfinished:
        out.append(ev.plain(content.t("dialogue.signatory.resumed", rng)))
        out.append(ev.pause())
        if unfinished == "the_narrator":
            out.append(ev.effect("static", persist=True))
        # The restart has to weigh him the same way the first attempt did,
        # or walking out and back in would be a way to shed the penalty.
        bonus = _narrator_penalty(state) if unfinished == "the_narrator" else 0
        out.extend(combat_mod.start_combat(state, content, rng, [unfinished],
                                           source_room=room_id,
                                           bonus_hp=bonus))
        return

    # Boss
    boss_id = room.get("boss")
    if boss_id and not state.flags.get(f"defeated.{boss_id}"):
        narrate_floor(state, content, rng, "boss_intro", out,
                      clause=content.t(floor["name_key"]),
                      boss=content.t(content.monster(boss_id)["name_key"]))
        out.append(ev.pause())
        out.extend(combat_mod.start_combat(state, content, rng, [boss_id],
                                           source_room=room_id))
        return

    # Miniboss
    mini_id = room.get("miniboss")
    if mini_id and not state.flags.get(f"defeated.{mini_id}"):
        out.append(ev.pause())
        out.extend(combat_mod.start_combat(state, content, rng, [mini_id],
                                           source_room=room_id))
        return

    # Elite guard
    elite_id = room.get("elite")
    if elite_id and not state.flags.get(f"defeated.{elite_id}"):
        out.extend(combat_mod.start_combat(state, content, rng, [elite_id],
                                           elite=True, source_room=room_id))
        return

    # Wandering encounter
    if room.get("kind") in ("normal", "dead_end") and not room.get("no_encounter"):
        chance = quirks.encounter_chance(content, state,
                                         floor.get("encounter_chance", 0.0))
        if state.flags.get("encounter_grace", 0) > 0:
            state.flags["encounter_grace"] -= 1
        elif rng.chance(chance):
            table = [(e["monster"], e["weight"]) for e in floor["encounter_table"]]
            monster_id = rng.weighted(table)
            carried = state.flags.pop(f"wounded.{monster_id}", None)
            count = 1
            mon = content.monster(monster_id)
            if mon.get("group") and rng.chance(0.35):
                count = 2
            out.append(ev.plain(content.t("encounter.ambush", rng)))
            out.extend(combat_mod.start_combat(
                state, content, rng, [monster_id] * count,
                carried_hp=carried, source_room=room_id))
            state.flags["encounter_grace"] = 1


# ------------------------------------------------------------------ combat
def _revive_if_downed(state, content, out):
    if not state.flags.pop("downed", False):
        return
    state.player.death_saves = {"pass": 0, "fail": 0}
    state.player.hp = max(1, state.player.hp)
    out.append(ev.plain(content.t("combat.up_after_fight")))


def _finish_combat(state, content, rng, out):
    combat = state.combat
    if combat is None:
        return
    _revive_if_downed(state, content, out)
    total_xp = 0
    drops = []
    for enemy in combat.enemies:
        mon = content.monster(enemy.monster_id)
        total_xp += mon.get("xp", 0) * (2 if enemy.elite else 1)
        state.flags[f"defeated.{enemy.monster_id}"] = True
        if mon.get("loot_table"):
            drops.extend(loot_mod.roll_table(content, rng, mon["loot_table"]))
        if enemy.elite and mon.get("elite_loot"):
            drops.extend(loot_mod.roll_table(content, rng, mon["elite_loot"]))

    clips = 0
    for enemy in combat.enemies:
        mon = content.monster(enemy.monster_id)
        low, high = mon.get("clips", [0, 0])
        if high:
            clips += rng.randint(low, high) * (2 if enemy.elite else 1)

    room_stash = f"room.{combat.source_room or state.room}"
    out.append(ev.combat_ended("won", total_xp, drops))
    if clips:
        state.currency += clips
        out.append(ev.currency_changed(clips, state.currency, "salvage",
                              shop_mod.currency_name(content, 2)))
    boss_room = content.floor(state.floor).get("boss_room")
    was_boss = combat.source_room == boss_room
    state.combat = None
    state.mode = MODE_EXPLORE
    state.player.statuses.clear()

    if drops:
        loot_mod.give(state, content, rng, drops, out, stash_key=room_stash)
    progression.award_xp(state, content, rng, total_xp, out)

    state.flags.pop("finale_fight", None)
    if any(e.monster_id == "the_narrator" for e in combat.enemies):
        out.append(ev.effect_end("static"))
        _narrator_beaten(state, content, rng, out)
        return
    if any(e.monster_id == "the_signatory" for e in combat.enemies):
        state.flags["ending"] = "fought"
        narrate(state, content, rng, "finale_fought", out)
        out.append(ev.pause())
    if was_boss or state.flags.get("ending"):
        _clear_floor(state, content, rng, out)


def _clear_floor(state, content, rng, out):
    floor = content.floor(state.floor)
    state.flags[f"floor_cleared.{state.floor}"] = True
    out.append(ev.floor_cleared(state.floor, content.t(floor["name_key"])))
    narrate_floor(state, content, rng, "floor_cleared", out,
                  clause=content.t(floor["name_key"]),
                  left=max(0, 13 - state.floor))
    art = content.get_art(floor.get("clear_art", ""))
    if art:
        out.append(ev.art_shown("clear", art))
    out.append(ev.pause())
    if floor.get("grants_continue"):
        state.continue_available = True
        out.append(ev.plain(content.t("progress.continue_granted")))

    next_floor = state.floor + 1
    if floor.get("last") or next_floor not in content.floors:
        state.mode = MODE_WON
        reason = "cleared" if floor.get("last") else "floor_complete"
        out.append(ev.run_ended(reason, state.stats.to_dict()))
        return

    descend(state, content, rng, next_floor, out)


def descend(state, content, rng, floor_n, out):
    """Move to the next floor. Stalkers do not follow you down."""
    leaving = content.floor(state.floor)
    if floor_effects(leaving):
        # Tidy up in case a burst was still running (a quick player can
        # clear a boss inside 30 seconds of the threshold burst firing).
        state.flags.pop(f"effect_threshold.{state.floor}", None)
        for name in floor_effects(leaving):
            out.append(ev.effect_end(name))
    stalker_mod.clear_stalkers(state, content, out, reason="descend")
    if state.companion.cid and not state.companion.alive:
        state.companion.alive = True
        state.companion.hp = max(1, state.companion.hp_max // 2)
        out.append(ev.plain(content.t(
            "companion.rejoins",
            name=content.t(content.companions[state.companion.cid]["name_key"]))))
    state.floor = floor_n
    floor = content.floor(floor_n)
    state.path_history = []
    out.append(ev.plain(content.t("progress.descend",
                                  clause=content.t(floor["clause_key"]))))
    narrate(state, content, rng, "descend", out)
    out.append(ev.pause())
    enter_room(state, content, rng, floor["start"], "", out)


# ------------------------------------------------------------------ finale
FINALE_NODES = {
    # node: (text key, [(command, label key, next node or ending)])
    "open":  ["dialogue.signatory.open",
              [("ask", "dialogue.signatory.opt_ask", "ask"),
               ("sign", "dialogue.signatory.opt_sign", "confirm_sign"),
               ("refuse", "dialogue.signatory.opt_refuse", "confirm_fight")]],
    "ask":   ["dialogue.signatory.ask",
              [("eleven", "dialogue.signatory.opt_eleven", "eleven"),
               ("sign", "dialogue.signatory.opt_sign", "confirm_sign"),
               ("refuse", "dialogue.signatory.opt_refuse", "confirm_fight")]],
    "eleven": ["dialogue.signatory.eleven",
               [("sign", "dialogue.signatory.opt_sign", "confirm_sign"),
                ("refuse", "dialogue.signatory.opt_refuse", "confirm_fight")]],
    "confirm_sign": ["dialogue.signatory.confirm_sign",
                     [("yes", "dialogue.signatory.opt_yes", "END_SIGN"),
                      ("no", "dialogue.signatory.opt_no", "open")]],
    "confirm_fight": ["dialogue.signatory.confirm_fight",
                      [("yes", "dialogue.signatory.opt_yes", "END_FIGHT"),
                       ("no", "dialogue.signatory.opt_no", "open")]],
}


def _finale_options(state, content, node):
    """Build the option list, adding the withdrawal only if it can be used."""
    options = list(FINALE_NODES[node][1])
    if (node in ("open", "ask", "eleven")
            and state.has_item("unamended_term")):
        options.insert(-2, ("withdraw", "dialogue.signatory.opt_withdraw",
                            "END_WITHDRAW"))
    return options


def _finale_show(state, content, rng, node, out):
    options = _finale_options(state, content, node)
    # He talks at length, so the text carries <pause> breaks like the long
    # NPC intros do. Arriving as one wall was the last thing this scene
    # needed after twelve floors.
    body = content.t(FINALE_NODES[node][0], rng)
    parts = [p.strip() for p in body.split("<pause>") if p.strip()]
    for i, part in enumerate(parts):
        if i:
            out.append(ev.pause())
        out.append(ev.plain(part))
    lines = [content.t("dialogue.signatory.prompt")]
    for command, label_key, _ in options:
        lines.append(f"  {command.upper()} - {content.t(label_key)}")
    out.append(ev.block("\n".join(lines)))
    state.pending = {"kind": "finale", "node": node,
                     "options": [c for c, _, _ in options]}
    state.mode = MODE_CHOICE


def _final_banner(state, content, out, verb="DISPUTED"):
    """The Clause 13 banner, whichever way the run actually ended.

    Only the fight raised it, because only the fight went through
    _clear_floor - so signing, walking out with the recording, walking out
    without it, and turning round and going back up all arrived with no
    banner and no floor_cleared effect in the browser.

    The verb changes with the ending, because four of the five are not
    disputes: you signed, or you left, or you went back up the stairs.
    """
    floor = content.floor(state.floor)
    out.append(ev.floor_cleared(state.floor, content.t(floor["name_key"]),
                                verb=verb))


def _finale_open(state, content, rng, out):
    """The last room, staged rather than dumped.

    The room description has just printed, so the first stop is there. Then
    it opens like an encounter - after twelve floors that is what the player
    is braced for - and only after the narrator has had his say does it turn
    out to be a man at a desk. Each beat is a press, roughly one every
    thirty lines at a narrow width, because this is the only scene in the
    game that earns the wait.
    """
    out.append(ev.pause())                       # after the room and its exits

    out.append(ev.plain(content.t("dialogue.signatory.presence", rng)))
    out.append(ev.pause())

    narrate(state, content, rng, "finale_intro", out)
    out.append(ev.pause())

    art = content.get_art("boss_signatory")
    if art:
        out.append(ev.art_shown("signatory", art))
    out.append(ev.plain(content.t("dialogue.signatory.reveal", rng)))
    out.append(ev.pause())

    _finale_show(state, content, rng, "open", out)


def _finale_step(state, content, rng, answer, out):
    node = state.pending["node"]
    options = _finale_options(state, content, node)
    chosen = None
    for command, _, target in options:
        if answer == command or (answer.isdigit()
                                 and int(answer) == options.index(
                                     (command, _, target)) + 1):
            chosen = target
            break
    if chosen is None:
        out.append(ev.error(content.t("dialogue.signatory.again")))
        _finale_show(state, content, rng, node, out)
        return

    if not chosen.startswith("END_"):
        _finale_show(state, content, rng, chosen, out)
        return

    state.pending = None
    state.flags["finale_done"] = True
    if chosen == "END_FIGHT":
        state.mode = MODE_EXPLORE
        out.append(ev.plain(content.t("dialogue.signatory.fight_start")))
        out.append(ev.pause())
        state.flags["finale_fight"] = "the_signatory"
        out.extend(combat_mod.start_combat(state, content, rng,
                                           ["the_signatory"],
                                           source_room=state.room))
        return

    if chosen == "END_SIGN":
        state.flags["ending"] = "signed"
        out.append(ev.plain(content.t("dialogue.signatory.end_signed")))
        narrate(state, content, rng, "finale_signed", out)
        out.append(ev.pause())
        _final_banner(state, content, out, verb="SIGNED")
        state.mode = MODE_WON
        out.append(ev.run_ended("signed", state.stats.to_dict()))
        return

    # Withdrawal works. That is the problem.
    out.append(ev.effect("signature", seconds=6))
    out.append(ev.plain(content.t("dialogue.signatory.end_withdrawn")))
    out.append(ev.pause())
    _narrator_turns(state, content, rng, out)


UNFILED_NARRATOR_HP = 100


def _narrator_penalty(state):
    """Extra health the Narrator carries if the withdrawal was never filed.

    The unamended term comes off The Amendment automatically and cannot be
    missed, so on its own it makes the withdrawal the default rather than
    something earned. The thirteen refusals are the part the memos build up
    on every floor, and until now they bought nothing but a keepsake.

    So the egg is not a gate — the ending stays reachable either way, which
    matters because a locked-out player would have nothing on screen telling
    them why. It is a discount. Say it on every floor and you fight him at
    his written weight; skip it and he is a hundred health heavier, which is
    two or three more rounds at the point in the game where rounds are the
    expensive thing.
    """
    return 0 if state.has_item("notice_of_withdrawal") else UNFILED_NARRATOR_HP


def _narrator_turns(state, content, rng, out):
    """The commentary track has been in this building since before the
    acquisition, and you have just done the one thing it cannot."""
    state.flags["narrator_boss"] = True
    # In his own framing and colour. He has been the marked, dimmed voice for
    # thirteen floors; the one moment he turns on you is the worst possible
    # time to start printing him as ordinary prose, because the whole point
    # is recognising who it is.
    for beat in ("turn_1", "turn_2", "turn_3", "turn_4"):
        out.append(ev.narration(content.t(f"dialogue.narrator.{beat}")))
        out.append(ev.pause())

    # The withdrawal has taken effect. You are, briefly, whole.
    state.player.hp = state.player.hp_max
    state.player.statuses.clear()
    state.companion.alive = True
    state.companion.hp = state.companion.hp_max
    progression.restore_ability_uses(state, content)
    out.append(ev.narration(content.t("dialogue.narrator.restored")))
    out.append(ev.effect("static", persist=True))
    state.flags["finale_fight"] = "the_narrator"
    out.extend(combat_mod.start_combat(state, content, rng, ["the_narrator"],
                                       source_room=state.room,
                                       bonus_hp=_narrator_penalty(state)))


def _narrator_fled(state, content, rng, out):
    """The fourth ending. You do not beat him and you do not sign.

    Running from the commentary is the eleventh disputant's answer: get to
    the end, turn round, and go back up. It is not an escape - nobody has
    ever left - but it is the only thing in the building that has made any
    difference to anybody, and the memos you have been reading all game were
    written by the last person who did it.
    """
    state.flags.pop("finale_fight", None)
    out.append(ev.effect_end("static"))
    state.flags["ending"] = "upstairs"
    state.flags["finale_done"] = True
    out.append(ev.narration(content.t("dialogue.narrator.fled_1")))
    out.append(ev.pause())
    out.append(ev.plain(content.t("dialogue.narrator.fled_2")))
    out.append(ev.pause())
    out.append(ev.plain(content.t("dialogue.narrator.fled_3")))
    out.append(ev.pause())
    out.append(ev.effect("party", seconds=12))
    out.append(ev.plain(content.t("dialogue.narrator.fled_4")))
    _final_banner(state, content, out, verb="ADJOURNED")
    state.mode = MODE_WON
    out.append(ev.run_ended("upstairs", state.stats.to_dict()))


def _narrator_beaten(state, content, rng, out):
    out.append(ev.narration(content.t("dialogue.narrator.beaten")))
    out.append(ev.pause())
    state.pending = {"kind": "narrator_end",
                     "options": ["leave", "take"]}
    state.mode = MODE_CHOICE
    lines = [content.t("dialogue.narrator.prompt"),
             f"  LEAVE - {content.t('dialogue.narrator.opt_leave')}",
             f"  TAKE  - {content.t('dialogue.narrator.opt_take')}"]
    out.append(ev.block("\n".join(lines)))


def _narrator_end(state, content, rng, answer, out):
    """LEAVE or TAKE. Both are the end of Clause 13, so both get the banner."""
    if answer not in ("leave", "take"):
        out.append(ev.error(content.t("dialogue.signatory.again")))
        return
    state.pending = None
    state.flags["ending"] = f"free_{answer}"
    out.append(ev.plain(content.t(f"dialogue.narrator.end_{answer}")))
    out.append(ev.pause())
    _final_banner(state, content, out, verb="CLOSED")
    state.mode = MODE_WON
    out.append(ev.run_ended(f"free_{answer}", state.stats.to_dict()))


def _handle_death(state, content, rng, out):
    floor = content.floor(state.floor)
    can_continue = state.continue_available or floor.get("grants_continue_on_death")
    if can_continue and not state.continue_used:
        state.mode = MODE_CHOICE
        state.pending = {"kind": "continue",
                         "prompt": content.t("progress.continue_offer"),
                         "options": ["yes", "no"]}
        narrate(state, content, rng, "death_continue", out)
        out.append(ev.plain(state.pending["prompt"]))
        return
    state.mode = MODE_DEAD
    state.flags["dead"] = True
    art = content.get_art("death")
    if art:
        out.append(ev.art_shown("death", art))
    narrate(state, content, rng, "death", out)
    out.append(ev.run_ended("died", state.stats.to_dict()))


def _spend_continue(state, content, rng, out):
    floor = content.floor(state.floor)
    state.continue_used = True
    state.continue_available = False
    state.combat = None
    state.flags.pop("downed", None)
    state.player.death_saves = {"pass": 0, "fail": 0}
    state.player.hp = max(1, state.player.hp_max // 2)
    state.player.statuses.clear()
    stalker_mod.clear_stalkers(state, content, out, reason="continue")
    progression.restore_ability_uses(state, content)
    state.mode = MODE_EXPLORE
    out.append(ev.continue_spent(state.floor))
    narrate(state, content, rng, "continue_spent", out)
    enter_room(state, content, rng, floor["start"], "", out)


# ------------------------------------------------------------------ helpers
def _show_memos(state, content, rng, want, out):
    """Everything read off a wall, listed, and one memo read back in full.

    MEMOS lists them; MEMOS <n> reprints one. They are kept out of the record
    because twenty-seven of them buried the keepsakes it exists to show.
    """
    keys = list(state.flags.get("notes", []))
    if not keys:
        out.append(ev.plain(content.t("labels.memos_none")))
        return

    want = (want or "").strip()
    if want:
        if not want.isdigit() or not 1 <= int(want) <= len(keys):
            out.append(ev.error(content.t("labels.memos_which",
                                          count=len(keys))))
            return
        out.append(ev.memo(content.t(keys[int(want) - 1], rng)))
        return

    # The opening clause is the memo's own description of itself ("Biro, on
    # a fire door:"), which makes a better label than a title nobody wrote.
    entries = [content.t(key, rng).split("\n\n")[0].strip().rstrip(":")
               for key in keys]
    out.append(ev.memo_list(entries,
                            content.t("labels.memos_header", count=len(keys)),
                            content.t("labels.memos_hint")))


def _list_abilities(state, content, want, out):
    """What ABILITY on its own, or an ambiguous ABILITY <partial>, shows.

    Uses left matter more than the flavour here, so this is the short form;
    SHEET still carries the full descriptions.
    """
    want = (want or "").lower().strip()
    rows = []
    for aid in state.player.abilities:
        spec = content.ability(aid)
        name = content.t(spec["name_key"])
        if want and want not in name.lower() and want not in aid.replace("_", " "):
            continue
        left = state.player.cooldowns.get(aid, spec.get("uses", 1))
        rows.append(f"  {title_case(name)}   {left}/{spec.get('uses', 1)} uses")
    if not rows:
        out.append(ev.error(content.t("errors.no_ability", name=want)))
        return
    header = content.t("labels.abilities_ambiguous" if want and len(rows) > 1
                       else "labels.abilities_header")
    out.append(ev.block(header + "\n\n" + "\n".join(rows)))


def _sheet_payload(state, content):
    cls = content.classes[state.player.cls]
    comp = content.companions.get(state.companion.cid, {})
    abilities = []
    for aid in state.player.abilities:
        spec = content.ability(aid)
        abilities.append({
            "id": aid,
            "name": content.t(spec["name_key"]),
            "desc": content.t(spec["desc_key"]),
            "uses_left": state.player.cooldowns.get(aid, 0),
            "uses_max": spec.get("uses", 1),
        })
    equipped = {}
    for slot, iid in state.equipped.items():
        item = content.item(iid)
        # A label, not a sentence, so it is cased like the record and the
        # inventory rather than like prose.
        label = title_case(content.t(item["name_key"]))
        if slot == "weapon":
            stat = item.get("stat", "str")
            bonus = state.player.mod(stat) + combat_mod.proficiency(state.player)
            label += (f"   {item.get('dmg', '1d4')}+{state.player.mod(stat)} dmg"
                      f", {bonus:+d} to hit ({stat.upper()})")
        elif item.get("ac"):
            label += f"   +{item['ac']} AC"
        equipped[slot] = label
    nxt = progression.next_threshold(state.player.level)
    return {
        "name": state.player.name,
        "cls": content.t(cls["name_key"]),
        "level": state.player.level,
        "xp": state.player.xp,
        "xp_next": nxt,
        "hp": state.player.hp, "hp_max": state.player.hp_max,
        "ac": combat_mod.player_ac(state, content),
        "stats": dict(state.player.stats),
        "abilities": abilities,
        "equipped": equipped,
        "companion": {
            "name": content.t(comp["name_key"]) if comp else "-",
            "hp": state.companion.hp, "hp_max": state.companion.hp_max,
            "alive": state.companion.alive,
            "passive": content.t(comp["passive_key"]) if comp else "",
        },
        "statuses": dict(state.player.statuses),
        "floor": state.floor,
        "currency": state.currency,
        "currency_name": shop_mod.currency_name(content, state.currency),
        "keepsake_count": len(state.keepsakes),
        "carl": bool(state.flags.get("carl")),
        "stalkers": [{"name": s.name, "distance": s.distance} for s in state.stalkers],
        "continue_available": state.continue_available and not state.continue_used,
    }


def _inventory_payload(state, content):
    """Anything in the bag is in the bag: equipping always removes it.

    A second copy of what you are wearing is a spare, and used to be tagged
    "equipped" because the check only compared ids - which read as the
    equipped armour never having left the bag at all.
    """
    items = []
    for entry in state.inventory:
        item = content.item(entry["id"])
        items.append({
            "id": entry["id"], "qty": entry["qty"],
            "name": content.t(item["name_key"]),
            "desc": content.t(item["desc_key"]),
            "usable": bool(item.get("use")),
            "equippable": bool(item.get("slot")),
            "equipped": False,
            "spare": state.equipped.get(item.get("slot")) == entry["id"],
        })
    return items


def _consume(state, entry, use, out):
    if use.get("consumed", True):
        state.remove_item(entry["id"])


def _is_free_action(state, content, item_id):
    """Combat buffs do not cost the round."""
    for entry in list(state.inventory) + [{"id": i} for i in state.keepsakes]:
        item = content.item(entry["id"])
        names = [entry["id"], content.t(item["name_key"]).lower()]
        names += item.get("aliases", [])
        if item_id.lower() in names or any(item_id.lower() in n for n in names):
            return (item.get("use", {}).get("op") == "status_self"
                    or item.get("use", {}).get("free_action"))
    # It may already have been consumed by the time we look.
    return False


def _use_item(state, content, rng, item_id, out):
    match = None
    for entry in state.inventory:
        if entry["id"] == item_id or item_id in content.item(entry["id"]).get("aliases", []):
            match = entry
            break
    if match is None:
        for iid in state.keepsakes:
            item = content.item(iid)
            if item_id == iid or item_id in item.get("aliases", []):
                match = {"id": iid, "qty": 1}
                break
    if match is None:
        out.append(ev.error(content.t("errors.no_item")))
        return
    item = content.item(match["id"])
    name = content.t(item["name_key"])
    use = item.get("use")
    if not use:
        out.append(ev.error(content.t("errors.not_usable", name=name)))
        return

    # Refuse rather than consume. Nothing here is common enough to burn on nothing.
    if use["op"] == "heal" and state.player.hp >= state.player.hp_max:
        out.append(ev.error(content.t("errors.already_full", name=name)))
        return
    if use["op"] == "status_self" and state.mode != MODE_COMBAT:
        out.append(ev.error(content.t("errors.combat_only", name=name)))
        return
    if use["op"] == "status_self" and use["status"] in state.player.statuses:
        out.append(ev.error(content.t("errors.already_active", name=name,
                                      status=use["status"])))
        return
    if use["op"] == "reveal" and state.flags.get(use["flag"]):
        out.append(ev.error(content.t("errors.already_read", name=name)))
        return

    if use["op"] == "stat_up":
        stat = use.get("stat", "all")
        stats = ["str", "dex", "con", "int", "cha"] if stat == "all" else [stat]
        for stat_name in stats:
            state.player.stats[stat_name] += use.get("amount", 1)
        if "con" in stats:
            state.player.hp_max += 2 * use.get("amount", 1)
            state.player.hp += 2 * use.get("amount", 1)
        out.append(ev.plain(content.t("loot.stat_up")))
        _consume(state, match, use, out)
        return

    if use["op"] == "currency_multiplier":
        state.flags["sell_multiplier"] = use.get("factor", 1.5)
        out.append(ev.plain(content.t("loot.franchise")))
        _consume(state, match, use, out)
        return

    if use["op"] == "grant_continue":
        if state.continue_available:
            out.append(ev.error(content.t("loot.already_continue")))
            return
        state.continue_available = True
        out.append(ev.plain(content.t("loot.second_opinion")))
        _consume(state, match, use, out)
        return

    if use["op"] == "heal_full":
        if state.player.hp >= state.player.hp_max:
            out.append(ev.error(content.t("errors.no_waste")))
            return
        healed = state.player.hp_max - state.player.hp
        state.player.hp = state.player.hp_max
        if use.get("grants_flag"):
            state.flags[use["grants_flag"]] = True
        out.append(ev.plain(content.t("combat.healed", amount=healed,
                                      hp=state.player.hp,
                                      max=state.player.hp_max)))
        _consume(state, match, use, out)
        return

    if use["op"] == "heal":
        from . import dice
        rolls, amount = dice.roll(use["dice"], rng)
        before = state.player.hp
        state.player.hp = min(state.player.hp_max, state.player.hp + amount)
        out.append(ev.dice_rolled(use["dice"], rolls, 0, amount, "healing"))
        out.append(ev.plain(content.t("combat.healed",
                                      amount=state.player.hp - before,
                                      hp=state.player.hp, max=state.player.hp_max)))
        if state.flags.get("downed") and state.player.hp > 0:
            state.flags.pop("downed")
            state.player.death_saves = {"pass": 0, "fail": 0}
    elif use["op"] == "status_self":
        state.player.statuses[use["status"]] = use.get("rounds", 2)
        out.append(ev.status_changed(state.player.name, use["status"], True,
                                     use.get("rounds", 2)))
    elif use["op"] == "reveal":
        state.flags[use["flag"]] = True
        out.append(ev.plain(content.t(use["text_key"])))
    if use.get("consumed", True):
        state.remove_item(match["id"])


# ------------------------------------------------------------------ step
def _migrate_keepsakes(state, content):
    """Pull key items out of the bag on saves written before the split."""
    if state.flags.get("keepsakes_migrated"):
        return
    state.flags["keepsakes_migrated"] = True
    for entry in list(state.inventory):
        if content.item(entry["id"]).get("key_item"):
            state.inventory.remove(entry)
            state.add_keepsake(entry["id"])


LOW_HP_FROM_FLOOR = 7


def _low_hp_watch(state, out):
    """Raise a held warning under a quarter health, drop it on recovery.

    Only from Clause 7 down: earlier floors are short enough that it would be
    noise rather than information. Also only while in combat: the vignette
    is a fight-tension cue, and it has no business lingering over the map
    once the fight that triggered it has ended.
    """
    if state.floor < LOW_HP_FROM_FLOOR or state.player.hp_max <= 0:
        return
    low = (state.mode == MODE_COMBAT
           and state.player.hp / state.player.hp_max < 0.25
           and state.player.hp > 0)
    if low and not state.flags.get("lowhp_shown"):
        state.flags["lowhp_shown"] = True
        out.append(ev.effect("lowhp", persist=True))
    elif not low and state.flags.get("lowhp_shown"):
        state.flags.pop("lowhp_shown", None)
        out.append(ev.effect_end("lowhp"))


def _effects_enabled(state):
    return state.settings.get("effects", "on") != "off"


def _filter_effects(out, state):
    """Strip the visual-effects events when the player has turned them off.

    Everything in EFFECTS.md's table (floor palettes, storm, lowhp, and so
    on) rides on these three event kinds. Cutting them here, in the engine,
    keeps the renderer honest: it still never has to look at GameState to
    decide what to show (see events.py), it just gets a shorter list.
    """
    if _effects_enabled(state):
        return out
    return [e for e in out
            if e.kind not in ("Effect", "EffectEnd", "PaletteChanged")]


def _effects_off_reset(state, content):
    """Tear down whatever is on screen the moment effects are switched off,
    instead of waiting for the next floor or fight to clear it naturally."""
    out = []
    if state.palette != "full":
        out.append(ev.palette_changed("full", notify=True))
    floor = content.floor(state.floor)
    for name in floor_effects(floor):
        out.append(ev.effect_end(name))
    if state.flags.get("lowhp_shown"):
        out.append(ev.effect_end("lowhp"))
        state.flags.pop("lowhp_shown", None)
    return out


def _effects_on_resync(state, content):
    """Mirror of resume(): re-tell the renderer the current palette and
    re-raise the floor's burst, since switching effects back on gets no
    other nudge otherwise."""
    out = [ev.palette_changed(state.palette,
                              notify=(state.palette != "rainbow"))]
    floor = content.floor(state.floor)
    for name in floor_effects(floor):
        out.append(ev.effect(name, seconds=floor_effect_seconds(floor)))
    return out


def step(state, action, content):
    _migrate_keepsakes(state, content)
    rng = _rng(state)
    out = []
    was_enabled = _effects_enabled(state)
    state.stats.turns += 1
    try:
        _dispatch(state, action, content, rng, out)
    finally:
        _sync(state, rng)
    _low_hp_watch(state, out)
    out = _filter_effects(out, state)
    now_enabled = _effects_enabled(state)
    if was_enabled and not now_enabled:
        out.extend(_effects_off_reset(state, content))
    elif now_enabled and not was_enabled:
        out.extend(_effects_on_resync(state, content))
    for event in out:
        if event.kind in ("Plain", "Narration", "Speech"):
            state.log(event.data.get("text", ""))
    return state, out


def _dispatch(state, action, content, rng, out):
    kind = action.kind

    # -- always available ----------------------------------------------
    if kind == "Sheet":
        out.append(ev.sheet(_sheet_payload(state, content)))
        return
    if kind == "Inventory":
        out.append(ev.inventory(_inventory_payload(state, content), state.cap()))
        return
    if kind == "Map":
        payload = mapview.build(state, content)
        out.append(payload if payload else mapview.unavailable_event(content))
        return
    if kind == "Withdraw":
        _withdraw(state, content, rng, out)
        return
    if kind == "Sing":
        _sing(state, content, rng, out)
        return
    if kind == "Photo":
        _photo(state, content, rng, out)
        return
    if kind == "Record":
        entries = [{"name": content.t(content.item(i)["name_key"]),
                    "desc": content.t(content.item(i)["desc_key"])}
                   for i in state.keepsakes]
        # Memos live under MEMOS, not here: there are twenty-seven of them
        # and they buried the keepsakes the record is actually for.
        notes = ([content.t("secret.carl_sheet")]
                 if state.flags.get("carl") else [])
        out.append(ev.record(entries, notes, state.currency,
                             shop_mod.currency_name(content, state.currency)))
        return
    if kind == "Memos":
        _show_memos(state, content, rng, action.arg, out)
        return
    if kind == "Portrait":
        cls = content.classes[state.player.cls]
        entries = [{
            "label": content.t("labels.you"),
            "name": f"{state.player.name}, {content.t(cls['name_key'])}",
            "art": content.get_art(cls.get("art", "")),
            "note": content.t(cls["desc_key"]),
        }]
        comp = content.companions.get(state.companion.cid)
        if comp:
            entries.append({
                "label": content.t("labels.companion"),
                "name": content.t(comp["name_key"]),
                "art": content.get_art(comp.get("art", "")),
                "note": f'{content.t(comp["desc_key"])}  '
                        f'{content.t(comp["passive_key"])}',
            })
        out.append(ev.portrait(entries))
        return
    if kind == "Explain":
        topic = action.arg or "combat"
        text = content.raw(f"help.{topic}")
        out.append(ev.block(text) if text
                   else ev.error(content.t("errors.no_topic", topic=topic)))
        return
    if kind == "Abilities":
        _list_abilities(state, content, action.arg, out)
        return
    if kind == "Setting":
        key, value = action.arg, action.extra.get("value")
        # A bare PACE or EFFECTS asks what the setting is, rather than being
        # a malformed attempt to change it. There was no way at all to find
        # out which pacing you were on, which matters: at `fast` every
        # press-enter break in the game is skipped, so a wall of intro text
        # arrives at once and looks like a missing pause rather than a
        # setting.
        if key == "pace" and not value:
            out.append(ev.plain(content.t(
                "settings.pace_now", pace=state.settings.get("pace", "slow"))))
        elif key == "pace" and value in ("fast", "slow", "manual"):
            state.settings["pace"] = value
            out.append(ev.plain(content.t("settings.pace_set", pace=value)))
        elif key == "effects" and not value:
            out.append(ev.plain(content.t(
                "settings.effects_now",
                state=state.settings.get("effects", "on"))))
        elif key == "effects" and value in ("on", "off"):
            state.settings["effects"] = value
            out.append(ev.plain(content.t(f"settings.effects_set_{value}")))
        elif key == "effects":
            out.append(ev.error(content.t("settings.effects_usage")))
        else:
            out.append(ev.error(content.t("settings.pace_usage")))
        return

    if state.mode == MODE_DEAD or state.mode == MODE_WON:
        out.append(ev.error(content.t("errors.run_over")))
        return

    # -- choice ---------------------------------------------------------
    if state.mode == MODE_CHOICE:
        if state.pending and state.pending.get("kind") == "narrator_end":
            _narrator_end(state, content, rng,
                          (action.arg or "").lower().strip(), out)
            return
        if state.pending and state.pending.get("kind") == "finale":
            answer = (action.arg or "").lower().strip()
            if kind not in ("Choose", "Continue", "Move", "Talk"):
                answer = ""
            _finale_step(state, content, rng, answer, out)
            return
        if kind in ("Choose", "Continue"):
            answer = action.arg.lower() if kind == "Choose" else "yes"
            if state.pending and state.pending["kind"] == "chit_retry":
                _chit_retry(state, content, rng, answer, out)
                return
            if state.pending and state.pending["kind"] == "continue":
                state.pending = None
                if answer in ("y", "yes"):
                    _spend_continue(state, content, rng, out)
                else:
                    state.mode = MODE_DEAD
                    state.flags["dead"] = True
                    narrate(state, content, rng, "death", out)
                    out.append(ev.run_ended("died", state.stats.to_dict()))
                return
        out.append(ev.error(content.t("errors.answer_first")))
        return

    # -- shop -----------------------------------------------------------
    if state.mode == MODE_SHOP:
        if kind == "Buy":
            shop_mod.buy(state, content, rng, action.arg, out)
        elif kind == "Sell":
            shop_mod.sell(state, content, rng, action.arg, out)
        elif kind in ("LeaveShop", "Move"):
            # Read the seller before clearing the shop, or a machine says
            # goodbye in the merchant's voice and with his words.
            machine = bool((state.shop or {}).get("machine"))
            who = shop_mod.seller_name(state, content)
            state.mode = MODE_EXPLORE
            state.shop = None
            out.append(ev.speech(who, content.t(
                "shop.machine_farewell" if machine else "shop.farewell", rng)))
            return
        else:
            out.append(ev.error(content.t("shop.usage")))
            return
        out.append(ev.shop(shop_mod.payload(state, content)))
        return

    # -- minigame -------------------------------------------------------
    if state.mode == MODE_MINIGAME:
        _minigame_step(state, content, rng, action, out)
        return

    # -- combat ---------------------------------------------------------
    if state.mode == MODE_COMBAT:
        _combat_step(state, content, rng, action, out)
        return

    # -- explore --------------------------------------------------------
    _explore_step(state, content, rng, action, out)


def _explore_step(state, content, rng, action, out):
    kind = action.kind
    floor = content.floor(state.floor)
    room = floor["rooms"][state.room]

    if kind == "Move":
        direction = DIRECTIONS.get(action.arg.lower())
        if not direction:
            out.append(ev.error(content.t("errors.bad_direction")))
            return
        dest = room.get("exits", {}).get(direction)
        if not dest:
            _walked_into_wall(state, content, rng, direction, out)
            return
        state.flags.pop("wall_bumps", None)
        enter_room(state, content, rng, dest, direction, out)
        return

    if kind == "Look":
        state.flags[f"looked.{state.room}"] = True
        describe_room(state, content, rng, state.room, True, out)
        return

    if kind == "Take":
        _take(state, content, rng, room, action.arg, out)
        return

    if kind == "Read":
        _read(state, content, rng, room, action.arg, out)
        return

    if kind == "Talk":
        _talk(state, content, rng, room, out)
        return

    if kind == "Rest":
        if room.get("kind") != "safe":
            out.append(ev.error(content.t("errors.not_safe")))
            return
        if state.flags.get(f"rested.{state.room}"):
            out.append(ev.plain(content.t("room.already_rested")))
            return
        state.flags[f"rested.{state.room}"] = True
        before = state.player.hp
        state.player.hp = state.player.hp_max
        state.companion.hp = state.companion.hp_max
        state.companion.alive = True
        if quirks.restores_abilities(content, state):
            progression.restore_ability_uses(state, content)
        else:
            out.append(ev.plain(content.t("quirk.no_ability_restore")))
        narrate(state, content, rng, "safe_room", out)
        out.append(ev.safe_room_rested(state.player.hp - before,
                                       state.player.hp, state.player.hp_max))
        return

    if kind == "Use":
        _use_item(state, content, rng, action.arg, out)
        return

    if kind == "Equip":
        _equip(state, content, action.arg, out)
        return

    if kind == "Drop":
        _drop_item(state, content, action.arg, out)
        return

    if kind == "Unequip":
        _unequip(state, content, action.arg, out)
        return

    if kind == "Wait":
        out.append(ev.plain(content.t("room.wait", rng)))
        return

    out.append(ev.error(content.t("errors.not_here")))


WALL_BUMPS_FOR_SECRET = 3


def _walked_into_wall(state, content, rng, direction, out):
    """Three goes at the same wall, in the same room, and it gives.

    Once per run. Persistence in a building like this ought to count for
    something and nothing else in thirteen floors rewards it.
    """
    out.append(ev.error(content.t("errors.no_exit", rng, direction=direction)))

    key = f"{state.room}:{direction}"
    if state.flags.get("wall_bumps_at") != key:
        state.flags["wall_bumps_at"] = key
        state.flags["wall_bumps"] = 1
        return
    bumps = state.flags.get("wall_bumps", 0) + 1
    state.flags["wall_bumps"] = bumps
    if bumps < WALL_BUMPS_FOR_SECRET or state.flags.get("wall_secret_found"):
        return

    state.flags["wall_secret_found"] = True
    state.flags.pop("wall_bumps", None)
    out.append(ev.plain(content.t("secret.wall_opens")))
    out.append(ev.pause())
    out.append(ev.plain(content.t("secret.wall_inside")))
    shop_mod.award(state, content, rng, 400, 700, out, "secret")
    loot_mod.give(state, content, rng, ["persistence"], out)
    narrate(state, content, rng, "wall_secret", out)


MERCH_TARGET = 5


def _check_merch(state, content, rng, out):
    """Five pieces of your own merchandise and something gives up on you.

    Before this the merch was three-paperclip vendor trash with no reason to
    keep any of it.
    """
    if state.flags.get("merch_reward"):
        return
    held = sum(e["qty"] for e in state.inventory if e["id"] == "own_merch")
    if held < MERCH_TARGET:
        return
    state.flags["merch_reward"] = True
    out.append(ev.pause())
    out.append(ev.plain(content.t("secret.merch_set")))
    loot_mod.give(state, content, rng, ["royalty_statement"], out)
    narrate(state, content, rng, "merch_set", out)


def _absorbs_pounce(state, content, out):
    """Grunk gets in the way of the first ambush on each floor."""
    comp = state.companion
    if not (comp.cid and comp.alive):
        return False
    if not content.companions[comp.cid].get("absorbs_pounce"):
        return False
    flag = f"pounce_absorbed.{state.floor}"
    if state.flags.get(flag):
        return False
    state.flags[flag] = True
    comp.hp = max(1, comp.hp - 6)
    out.append(ev.plain(content.t(
        "companion.absorbs_pounce",
        name=content.t(content.companions[comp.cid]["name_key"]))))
    return True


def _chest_quirk(state, content, rng, entry):
    """Deterministic per chest, so a reload cannot reroll it.

    Roughly one chest in four is jammed (Strength) and one in five is rigged
    (Intelligence to spot). Before this, STR and INT did nothing outside
    weapon damage and the whole of Pip's passive had nothing to act on.
    """
    seed = f"{entry.get('flag','')}{state.seed}"
    roll = sum(ord(ch) for ch in seed) % 20
    if roll < 4:
        return "jammed"
    if roll < 8:
        return "rigged"
    # Roughly one chest on every other floor stands up. Even floors only, so
    # it never happens twice in a row.
    if roll == 8 and state.floor % 2 == 0:
        return "mimic"
    return None


def _inspect_chest(state, content, rng, entry, out):
    """Pip looks first. Failing that, your own wits do."""
    quirk = _chest_quirk(state, content, rng, entry)
    comp = state.companion
    has_pip = (comp.cid and comp.alive
               and content.companions[comp.cid].get("inspects_chests"))
    if has_pip:
        name = content.t(content.companions[comp.cid]["name_key"])
        out.append(ev.speech(name, content.t(
            f"companions.pip.chest_{quirk or 'clear'}", rng)))
        return quirk, True

    if quirk == "rigged":
        roll = rng.d(20) + state.player.mod("int")
        spotted = roll >= 12
        out.append(ev.dice_rolled("d20", [roll - state.player.mod("int")],
                                  state.player.mod("int"), roll, "spot the catch"))
        if spotted:
            out.append(ev.plain(content.t("loot.spotted_rig")))
        return quirk, spotted
    return quirk, False


def _read(state, content, rng, room, target, out):
    """READ, for the things written on walls.

    TAKE opens chests and picks things up, which is the wrong verb for
    graffiti at knee height. This reads notes and nothing else, so READ in a
    room with a chest in it says there is nothing to read rather than
    quietly looting the chest.
    """
    notes = [e for e in room.get("contents", []) if e["type"] == "note"]
    if target:
        wanted = target.lower()
        notes = [e for e in notes if wanted in content.t(e["text_key"]).lower()]
    unread = [e for e in notes if not state.flags.get(e["flag"])]
    if not unread:
        out.append(ev.error(content.t(
            "errors.reread" if notes else "errors.nothing_to_read")))
        return
    for entry in unread:
        _read_note(state, content, rng, entry, out)


def _read_note(state, content, rng, entry, out):
    """Filing a note in the record. Shared by READ and TAKE."""
    state.flags[entry["flag"]] = True
    notes = list(state.flags.get("notes", []))
    if entry["text_key"] not in notes:
        notes.append(entry["text_key"])
        state.flags["notes"] = notes
    state.stats.notes_read += 1
    out.append(ev.plain(content.t("loot.note_read", rng)))
    out.append(ev.memo(content.t(entry["text_key"], rng), fresh=True))


def _take(state, content, rng, room, target, out):
    found = False
    # Anything a full pack left behind is still here: chest spill or the
    # drops from whatever you killed in this room.
    stash_keys = [f"room.{state.room}"] + [
        e["flag"] for e in room.get("contents", []) if e.get("flag")]
    for key in stash_keys:
        stash = state.flags.get(f"stash.{key}")
        if stash:
            found = True
            out.append(ev.plain(content.t("loot.stash_here")))
            loot_mod.give(state, content, rng, list(stash), out, stash_key=key)
    for entry in room.get("contents", []):
        if entry["type"] == "chest" and not state.flags.get(entry["flag"]):
            quirk, forewarned = _inspect_chest(state, content, rng, entry, out)
            if quirk == "jammed":
                roll = rng.d(20) + state.player.mod("str")
                out.append(ev.dice_rolled("d20", [roll - state.player.mod("str")],
                                          state.player.mod("str"), roll,
                                          "force it open"))
                if roll < 12:
                    out.append(ev.plain(content.t("loot.jammed_fail")))
                    return
                out.append(ev.plain(content.t("loot.jammed_open")))
            elif quirk == "rigged" and not forewarned:
                rolls, hurt = dice.roll("3d6", rng)
                combat_mod.hurt_player(state, content, hurt, out)
                out.append(ev.plain(content.t("loot.rigged", amount=hurt)))
            elif quirk == "rigged":
                out.append(ev.plain(content.t("loot.rig_disarmed")))
            elif quirk == "mimic":
                state.flags[entry["flag"]] = True
                out.append(ev.plain(content.t("loot.mimic", rng)))
                out.append(ev.pause())
                out.extend(combat_mod.start_combat(
                    state, content, rng, ["the_contents"],
                    surprised=not forewarned, source_room=state.room))
                return
            found = True
            state.flags[entry["flag"]] = True
            state.stats.chests_opened += 1
            if state.companion.cid == "pip":
                out.append(ev.speech(content.t(content.companions["pip"]["name_key"]),
                                     content.t("companion.pip.peek", rng)))
            out.append(ev.plain(content.t(entry.get("open_key", "loot.chest_open"), rng)))
            table = content.loot.get(entry["loot_table"], {})
            clips = table.get("clips")
            if clips:
                shop_mod.award(state, content, rng, clips[0], clips[1], out, "chest")
            drops = loot_mod.roll_table(content, rng, entry["loot_table"])
            if drops:
                loot_mod.give(state, content, rng, drops, out,
                              stash_key=entry["flag"])
            elif not clips:
                out.append(ev.plain(content.t("loot.empty", rng)))
        elif entry["type"] == "note" and not state.flags.get(entry["flag"]):
            # Notes are flavour that persists: reading one files it in the
            # record, so the walls of the building slowly become a document
            # you are carrying rather than scenery you walked past. READ is
            # the natural verb and does the same thing.
            found = True
            _read_note(state, content, rng, entry, out)
        elif entry["type"] == "stash" and not state.flags.get(entry["flag"]):
            # Announced by nothing. The room description does not mention it
            # and neither does anything on entry: TAKE in a room that looks
            # empty is the only way it is ever found, which is what makes
            # trying it worth the habit.
            found = True
            state.flags[entry["flag"]] = True
            state.stats.stashes_found += 1
            out.append(ev.plain(content.t(entry.get("text_key",
                                                    "loot.stash_found"), rng)))
            shop_mod.award(state, content, rng,
                           entry.get("low", 20), entry.get("high", 45),
                           out, "stash")
        elif entry["type"] == "item" and not state.flags.get(entry["flag"]):
            found = True
            state.flags[entry["flag"]] = True
            loot_mod.give(state, content, rng, [entry["item"]], out)
    if found:
        _check_merch(state, content, rng, out)
    if not found:
        out.append(ev.plain(content.t("loot.nothing_here", rng)))


WITHDRAW_TARGET = 13
SING_TARGET = 3
PHOTO_TARGET = 5


def _withdraw(state, content, rng, out):
    """The thing Clause 1 asked and never offered a way to do.

    Thirteen refusals, **one per clause** — the count is of distinct floors,
    not of times said. Every memo frames it that way and the notice itself
    says so, so saying it thirteen times in the intake room should not have
    filed anything. Thirteen floors and a target of thirteen means the whole
    building, Floor 1 to Floor 13, with nothing spare.
    """
    floor_flag = f"withdrew.{state.floor}"
    count = state.flags.get("withdraw_count", 0)

    if count >= WITHDRAW_TARGET:
        out.append(ev.plain(content.t("secret.withdraw_after", rng)))
        return

    # Said before on this floor: answer it, because it is a thing the player
    # is meant to keep doing, but do not advance the count.
    if state.flags.get(floor_flag):
        out.append(ev.plain(content.t("secret.withdraw_repeat", rng)))
        return

    state.flags[floor_flag] = True
    count += 1
    state.flags["withdraw_count"] = count
    out.append(ev.plain(content.t("secret.withdraw_said", rng)))
    narrate(state, content, rng, f"withdraw_{min(count, 4)}", out)
    if count == WITHDRAW_TARGET:
        out.append(ev.pause())
        out.append(ev.plain(content.t("secret.withdraw_filed")))
        loot_mod.give(state, content, rng, ["notice_of_withdrawal"], out)
        narrate(state, content, rng, "withdraw_filed", out)


def _sing(state, content, rng, out):
    """Only worth doing where nothing is trying to kill you."""
    room = content.room(state.floor, state.room)
    if room.get("kind") != "safe":
        out.append(ev.plain(content.t("secret.sing_anywhere", rng)))
        return
    rooms = set(state.flags.get("sang_in", []))
    if state.room in rooms:
        out.append(ev.plain(content.t("secret.sing_again", rng)))
        return
    rooms.add(state.room)
    state.flags["sang_in"] = sorted(rooms)
    out.append(ev.plain(content.t("secret.sing", rng)))
    if len(rooms) == SING_TARGET and not state.flags.get("sing_reward"):
        state.flags["sing_reward"] = True
        out.append(ev.pause())
        out.append(ev.effect("party"))
        out.append(ev.plain(content.t("secret.sing_joined")))
        shop_mod.award(state, content, rng, 150, 300, out, "secret")
        loot_mod.give(state, content, rng, ["harmony"], out)


def _photo(state, content, rng, out):
    """The beach photograph is in every safe room in the building."""
    room = content.room(state.floor, state.room)
    if room.get("kind") != "safe":
        out.append(ev.plain(content.t("secret.photo_none", rng)))
        return
    seen = set(state.flags.get("photo_floors", []))
    if state.floor in seen:
        out.append(ev.plain(content.t("secret.photo_again", rng)))
        return
    seen.add(state.floor)
    state.flags["photo_floors"] = sorted(seen)
    # One line per floor rather than a count that stuck at "fifth" forever.
    out.append(ev.plain(content.t(f"secret.photo_floor_{state.floor}")))
    if len(seen) == PHOTO_TARGET and not state.flags.get("photo_reward"):
        state.flags["photo_reward"] = True
        out.append(ev.pause())
        loot_mod.give(state, content, rng, ["the_photograph"], out)
        narrate(state, content, rng, "photo_found", out)


VOICES_THRESHOLD = 5
VOICES_PAYOFF = 8


def _talk_to_nobody(state, content, rng, out):
    """Keep talking to an empty room and something eventually answers.

    It is the building, or the other ten disputants, or nothing at all. The
    game never settles which, and neither do you. Talking back is optional;
    the voices do not require an audience and do not go away without one.
    """
    if state.flags.get("voices"):
        replies = state.flags.get("voice_replies", 0) + 1
        state.flags["voice_replies"] = replies
        out.append(ev.voice(content.t("voices.reply", rng)))
        if replies == VOICES_PAYOFF:
            state.player.stats["cha"] += 1
            out.append(ev.plain(content.t("voices.payoff")))
        return

    count = state.flags.get("nobody_talks", 0) + 1
    state.flags["nobody_talks"] = count
    if count < VOICES_THRESHOLD:
        out.append(ev.plain(content.t(f"room.no_one_{min(count, 4)}", rng)))
        return

    state.flags["voices"] = True
    state.flags["voice_replies"] = 0
    out.append(ev.plain(content.t("voices.onset")))
    out.append(ev.voice(content.t("voices.first", rng)))
    narrate(state, content, rng, "voices_onset", out)


def _companion_recover(state, content, out):
    """A living companion knits itself back together as you walk.

    A downed one does not: it is out of service until you REST in a safe
    room, which is the only thing that brings it back.
    """
    comp = state.companion
    if not comp.cid or not comp.alive:
        return
    if comp.hp >= comp.hp_max:
        return
    spec = content.companions[comp.cid]
    rate = spec.get("regen", 1)
    healed = min(rate, comp.hp_max - comp.hp)
    comp.hp += healed
    if comp.hp >= comp.hp_max:
        out.append(ev.plain(content.t("companion.recovered",
                                      name=content.t(spec["name_key"]))))


def _voices_ambient(state, content, rng, out):
    """Once they start, they do not stop. Called on every room entry."""
    if not state.flags.get("voices"):
        return
    if not rng.chance(0.18):
        return
    out.append(ev.voice(content.t("voices.ambient", rng)))


def _talk(state, content, rng, room, out):
    npc = None
    for entry in room.get("contents", []):
        if entry["type"] == "npc":
            npc = entry
            break
    if npc is None:
        _talk_to_nobody(state, content, rng, out)
        return

    spec = content.raw(f"npcs.{npc['id']}")
    first = not state.flags.get(f"talked.{npc['id']}")
    state.flags[f"talked.{npc['id']}"] = True
    art = content.get_art(spec.get("art", ""))
    if art and first:
        out.append(ev.art_shown(npc["id"], art))
    key = "intro" if first else "repeat"
    # A long intro can ask for press-enter breaks with <pause>, so a wall of
    # text arrives in readable pieces. The speaker is named once; the rest
    # are continuations of the same person talking. A marker at the very end
    # is a beat before whatever follows — usually a game starting — so it
    # still counts even though it has no text after it.
    raw = content.t(f"npcs.{npc['id']}.{key}", rng)
    parts = [p.strip() for p in raw.split("<pause>")]
    trailing = bool(parts and not parts[-1])
    parts = [p for p in parts if p]
    for i, part in enumerate(parts):
        if i:
            out.append(ev.pause())
            # Plain, not block: a block hangs its continuation lines, which
            # would leave the second half of one person's speech indented
            # differently from the first. The leading newline keeps the
            # paragraph break, since a pause prints nothing on `pace fast`.
            out.append(ev.plain("\n" + part))
        else:
            out.append(ev.speech(spec["name"], part))
    if trailing:
        out.append(ev.pause())

    if npc.get("shop"):
        shop_mod.open_shop(state, content, rng, npc.get("config", {}))
        state.mode = MODE_SHOP
        # A beat between what he says and the shelf. Without it his greeting
        # scrolls off under the stock list and the player never reads it.
        # Skipped when the intro already ended on one, and by `pace fast`.
        if not trailing:
            out.append(ev.pause())
        out.append(ev.shop(shop_mod.payload(state, content)))
        return

    if npc.get("minigame") and not state.flags.get(f"minigame_done.{npc['id']}"):
        game = minigames.get(npc["minigame"])
        state.minigame = game.start(state, content, rng, npc.get("config", {}))
        state.minigame["npc"] = npc["id"]
        state.minigame["game"] = npc["minigame"]
        # Kept so the settlement chit can restart the same game against the
        # same opponent; start() reads it and does not store it itself.
        state.minigame["config"] = npc.get("config", {})
        state.mode = MODE_MINIGAME
        # Same beat before the board appears: the rules and the challenge are
        # in what they just said, and the prompt buries them otherwise.
        if not trailing:
            out.append(ev.pause())
        out.append(ev.minigame_prompt(game.prompt(state, content, state.minigame)))
    elif npc.get("gives") and not state.flags.get(f"gift.{npc['id']}"):
        state.flags[f"gift.{npc['id']}"] = True
        loot_mod.give(state, content, rng, [npc["gives"]], out)


def _unequip(state, content, slot_or_item, out):
    want = (slot_or_item or "").lower().strip()
    for slot, iid in list(state.equipped.items()):
        name = content.t(content.item(iid)["name_key"]).lower()
        if want in (slot, iid, name) or (want and want in name) or not want:
            if state.inventory_full():
                out.append(ev.error(content.t("errors.no_room_unequip")))
                return
            del state.equipped[slot]
            state.add_item(iid)
            out.append(ev.plain(content.t(
                "loot.unequipped", name=content.t(content.item(iid)["name_key"]))))
            return
    out.append(ev.error(content.t("errors.not_equipped")))


def _drop_item(state, content, item_id, out):
    """Never drop a key item, and never leave a dropped thing equipped."""
    match = None
    for entry in state.inventory:
        item = content.item(entry["id"])
        name = content.t(item["name_key"]).lower()
        if item_id.lower() in (entry["id"], name) or (
                item_id and item_id.lower() in name):
            match = entry
            break
    if match is None:
        out.append(ev.error(content.t("errors.no_item")))
        return

    item = content.item(match["id"])
    name = content.t(item["name_key"])
    if item.get("key_item"):
        out.append(ev.error(content.t("errors.cannot_drop", name=name)))
        return

    state.remove_item(match["id"])
    out.append(ev.plain(content.t("loot.dropped_named", name=name)))


def _equip(state, content, item_id, out):
    for entry in state.inventory:
        if entry["id"] == item_id:
            item = content.item(item_id)
            slot = item.get("slot")
            if not slot:
                out.append(ev.error(content.t("errors.not_equippable")))
                return
            previous = state.equipped.get(slot)
            if previous == item_id:
                out.append(ev.plain(content.t("loot.already_equipped",
                                              name=content.t(item["name_key"]))))
                return
            # Removing first frees a slot, but only when this was the last of
            # a stack. Two of the same jacket meant the old one was handed to
            # a full bag, silently dropped on the floor by add_item, and gone.
            state.remove_item(item_id)
            if previous and state.inventory_full():
                state.add_item(item_id)
                out.append(ev.error(content.t("errors.no_room_swap")))
                return
            state.equipped[slot] = item_id
            out.append(ev.plain(content.t("loot.equipped",
                                          name=content.t(item["name_key"]))))
            if previous:
                state.add_item(previous)
                out.append(ev.plain(content.t(
                    "loot.stowed",
                    name=content.t(content.item(previous)["name_key"]))))
            return
    out.append(ev.error(content.t("errors.no_item")))


def _combat_step(state, content, rng, action, out):
    kind = action.kind
    acted = False
    if kind in ("Attack", "Ability", "Flee", "Use"):
        out.append(ev.turn_started(state.player.name, "player",
                                   state.combat.round if state.combat else 1))

    if kind == "Attack":
        out.extend(combat_mod.player_attack(state, content, rng, action.arg))
        acted = True
    elif kind == "Ability":
        before = len(out)
        out.extend(combat_mod.use_ability(state, content, rng, action.arg,
                                          action.extra.get("target", "")))
        acted = not any(e.kind == "Error" for e in out[before:])
    elif kind == "Flee":
        fleeing = [e.monster_id for e in state.combat.enemies]
        out.extend(combat_mod.player_flee(state, content, rng))
        if state.mode == MODE_EXPLORE:
            if "the_narrator" in fleeing:
                _narrator_fled(state, content, rng, out)
            return
        acted = True
    elif kind == "Use":
        before = len(out)
        _use_item(state, content, rng, action.arg, out)
        failed = any(e.kind == "Error" for e in out[before:])
        # A buff that costs you the round is a buff nobody drinks. Statuses
        # are free; healing still costs you the turn.
        free = _is_free_action(state, content, action.arg)
        acted = not failed and not free
    else:
        out.append(ev.error(content.t("errors.in_combat")))
        return

    if not acted:
        return

    if combat_mod.is_over(state):
        _finish_combat(state, content, rng, out)
        return

    if state.flags.get("pending_phase"):
        _enter_phase(state, content, rng, out)
        return

    events, awaiting = combat_mod.advance(state, content, rng)
    out.extend(events)

    if state.mode == MODE_DEAD:
        _handle_death(state, content, rng, out)
        return
    if combat_mod.is_over(state):
        _finish_combat(state, content, rng, out)


def _enter_phase(state, content, rng, out):
    """A boss half-beaten stops fighting and makes you do something else.

    Combat state is left intact underneath; the minigame resolves and hands
    control straight back to the fight.
    """
    phase = state.flags.pop("pending_phase")
    game = minigames.get(phase["game"])
    out.append(ev.block(content.t(phase["intro_key"])))
    state.minigame = game.start(state, content, rng, phase.get("config", {}))
    state.minigame["npc"] = f"phase.{phase['uid']}"
    state.minigame["game"] = phase["game"]
    state.minigame["phase"] = phase
    state.mode = MODE_MINIGAME
    # Mid-fight, so the intro is the only warning that the rules just changed.
    out.append(ev.pause())
    out.append(ev.minigame_prompt(game.prompt(state, content, state.minigame)))


def _resolve_phase(state, content, rng, mg, outcome, out):
    """Hand the fight back, with the minigame's result applied to it."""
    phase = mg["phase"]
    state.minigame = None
    state.mode = MODE_COMBAT
    enemy = state.combat.by_uid(phase["uid"]) if state.combat else None

    if outcome["won"]:
        state.stats.minigames_won += 1
        out.append(ev.block(content.t(phase["win_key"])))
        if enemy:
            damage = int(enemy.hp_max * phase.get("win_damage_pct", 0.25))
            enemy.hp = max(1, enemy.hp - damage)
            enemy.statuses["stunned"] = phase.get("win_stun", 1)
            out.append(ev.plain(content.t("combat.phase_won",
                                          name=enemy.name, amount=damage)))
    else:
        out.append(ev.block(content.t(phase["lose_key"])))
        cost = phase.get("lose_damage", 12)
        state.player.hp = max(1, state.player.hp - cost)
        out.append(ev.plain(content.t("combat.phase_lost", amount=cost,
                                      hp=state.player.hp)))

    if combat_mod.is_over(state):
        _finish_combat(state, content, rng, out)
        return
    events, awaiting = combat_mod.advance(state, content, rng)
    out.extend(events)
    if state.mode == MODE_DEAD:
        _handle_death(state, content, rng, out)
    elif combat_mod.is_over(state):
        _finish_combat(state, content, rng, out)


def _minigame_step(state, content, rng, action, out):
    mg = state.minigame
    game = minigames.get(mg["game"])
    if action.kind not in ("Minigame", "Choose"):
        out.append(ev.error(content.t("errors.in_minigame")))
        return
    mg, mg_events = game.step(state, content, rng, mg, action)
    out.extend(mg_events)
    state.minigame = mg

    outcome = game.result(mg)
    if outcome is None:
        out.append(ev.minigame_prompt(game.prompt(state, content, mg)))
        return

    if mg.get("phase"):
        _resolve_phase(state, content, rng, mg, outcome, out)
        return

    game_id = mg["game"]
    state.flags[f"minigame_done.{mg['npc']}"] = True
    state.mode = MODE_EXPLORE
    state.minigame = None
    if outcome["won"]:
        state.stats.minigames_won += 1
        out.append(ev.plain(content.t(f"minigame.{game_id}.victory", rng)))
        if mg.get("reward"):
            loot_mod.give(state, content, rng, [mg["reward"]], out)
        progression.award_xp(state, content, rng, mg.get("xp", 40), out)
    else:
        # The settlement chit is "redeemable against a claim you have not
        # yet made". This is the claim. Offered rather than spent for you,
        # because a one-off item that vanishes without being asked about is
        # worse than no item at all.
        if state.has_item("settlement_chit"):
            state.mode = MODE_CHOICE
            state.pending = {"kind": "chit_retry",
                             "prompt": content.t("items.settlement_chit.offer"),
                             "options": ["yes", "no"],
                             "npc": mg["npc"], "game": game_id,
                             "config": mg.get("config", {}),
                             "penalty": mg.get("penalty", 3),
                             "xp": mg.get("xp", 40),
                             "reward": mg.get("reward")}
            out.append(ev.plain(content.t(f"minigame.{game_id}.defeat", rng)))
            out.append(ev.plain(state.pending["prompt"]))
            return
        _minigame_loss(state, content, rng, game_id, mg.get("penalty", 3), out)


def _minigame_loss(state, content, rng, game_id, penalty, out):
    out.append(ev.plain(content.t(f"minigame.{game_id}.defeat", rng)))
    state.player.hp = max(1, state.player.hp - penalty)
    out.append(ev.plain(content.t(f"minigame.{game_id}.penalty",
                                  amount=penalty, hp=state.player.hp)))


def _chit_retry(state, content, rng, answer, out):
    """Spend the chit to replay the game that was just lost, or decline.

    The `minigame_done` flag is deliberately not set on the way in here, so
    a retry is a genuine second sitting rather than a re-entry into a game
    the building considers finished.
    """
    pending = state.pending
    state.pending = None
    if answer not in ("y", "yes"):
        state.mode = MODE_EXPLORE
        state.flags[f"minigame_done.{pending['npc']}"] = True
        state.player.hp = max(1, state.player.hp - pending["penalty"])
        out.append(ev.plain(content.t(f"minigame.{pending['game']}.penalty",
                                      amount=pending["penalty"],
                                      hp=state.player.hp)))
        return

    state.remove_item("settlement_chit")
    out.append(ev.plain(content.t("items.settlement_chit.spent")))
    game = minigames.get(pending["game"])
    state.mode = MODE_MINIGAME
    state.minigame = game.start(state, content, rng, pending["config"])
    state.minigame["npc"] = pending["npc"]
    state.minigame["game"] = pending["game"]
    state.minigame["config"] = pending["config"]
    state.minigame["penalty"] = pending["penalty"]
    state.minigame["xp"] = pending["xp"]
    if pending["reward"]:
        state.minigame["reward"] = pending["reward"]
    # Spent, so a second loss is a second loss.
    state.minigame["retried"] = True
    out.append(ev.pause())
    out.append(ev.minigame_prompt(game.prompt(state, content, state.minigame)))


def resume(state, content):
    """Events a frontend needs on starting or loading, to re-sync presentation.

    Palette re-syncs immediately: a loaded save has already passed the point
    that would normally have announced it, so without this the browser
    overlay is never told what palette is in force. The floor's effect
    re-fires here too, as a burst of ENTRANCE_EFFECT_SECONDS, so loading
    into any room on a floor sets the scene the same way arriving on it
    does. Purely presentational otherwise: nothing here changes game state.
    """
    out = [ev.text_speed(quirks.text_speed(content, state)),
           # Floor 7's spectrum is terminal-only (see enter_room); resuming
           # into it should not notify the browser any more than arriving
           # there fresh does.
           ev.palette_changed(state.palette, notify=(state.palette != "rainbow"))]
    floor = content.floor(state.floor)
    for name in floor_effects(floor):
        out.append(ev.effect(name, seconds=floor_effect_seconds(floor)))
    return _filter_effects(out, state)


# ------------------------------------------------------------------ start
def new_game(content, seed, name, class_id, companion_id, floor_n=1):
    from .state import GameState
    state = GameState()
    state.seed = seed
    state.content_version = content.version
    state.floor = floor_n
    rng = _rng(state)
    progression.build_player(state, content, rng, name, class_id, companion_id)
    floor = content.floor(floor_n)
    state.palette = floor.get("palette", "mono")
    out = []
    progression.carl_bonus(state, content, out)
    narrate(state, content, rng, "opening", out)
    enter_room(state, content, rng, floor["start"], "", out)
    _sync(state, rng)
    return state, _filter_effects(out, state)
