"""Build a save file for the start of each floor, fully progressed.

    python3 tools/make_saves.py                    # all thirteen
    python3 tools/make_saves.py --name Bud         # set the character name
    python3 tools/make_saves.py --class hexwright  # pick a class
    python3 tools/make_saves.py 7 8                # only these floors
    python3 tools/make_saves.py --no-eggs          # skip easter egg rewards

Each save puts you in the start room of floor N with everything from floors
1..N-1 done: every chest opened, every boss, miniboss and elite defeated, all
key items held, both earlier map tiers, the right level and gear, and a purse.
Floor N itself is untouched, so it plays exactly as a fresh arrival would.

Saves are built by running the real engine, so anything the engine would
refuse to produce cannot end up in one.
"""

import argparse
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import loot as loot_mod                          # noqa: E402
from engine import progression, saves, step as step_mod      # noqa: E402
from engine.content import load_from_disk                    # noqa: E402
from engine.rng import Rng                                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "saves")

# Key items each floor hands out, by the floor you get them ON.
FLOOR_KEY_ITEMS = {
    1: ["audit_trail", "greeter_badge"],
    2: ["toll_token", "ledger_page"],
    3: ["excess_clause"],
    4: ["extended_log", "hensley_keys"],
    5: ["parity_disk", "raid_head"],
    6: ["likeness_rights"],
    7: ["prism"],
    8: ["full_disclosure", "indemnity_waiver"],
    9: ["severed_half"],
    10: ["vorn_seal"],
    11: ["unamended_term"],
    12: ["reaper_scale"],
}

# Fallback weapon per class, used when the floor's best weapon scales off a
# stat the class does not have. A Hexwright's pen is useless to a Vanguard.
CLASS_WEAPON = {"vanguard": "crucible_hammer", "skirmisher": "secateurs",
                "hexwright": "red_pen", "advocate": "gavel"}

# Best gear available having cleared up to and including that floor.
GEAR_BY_FLOOR = {
    1: ("stapler", "ppe_vest"),
    2: ("coin_gauntlet", "waders"),
    3: ("actuary_pen", "loss_ledger"),
    4: ("secateurs", "gardening_gloves"),
    5: ("tape_flail", "rack_plate"),
    6: ("signature_pen", "brand_shield"),
    7: ("barometer", "storm_coat"),
    8: ("crucible_hammer", "slag_plate"),
    9: ("blue_pencil_item", "severance_plate"),
    10: ("gavel", "court_dress"),
    11: ("red_pen", "original_copy"),
    12: ("ash_blade", "shutter_plate"),
}

# Healing to arrive with, by the floor you are arriving on.
STOCK_BY_FLOOR = [
    (13, "good_tea"), (12, "last_kettle"), (11, "clean_draft"),
    (10, "stay_of_execution"), (9, "whole_thing"), (8, "quench"),
    (7, "eye_of_the_storm"), (6, "royalty_cheque"), (5, "cold_backup"),
    (4, "casserole"), (3, "cold_tea"), (2, "sump_water"), (1, "ration"),
]

EGG_ITEMS = ["persistence", "notice_of_withdrawal", "harmony", "the_photograph"]

# Level to arrive on each floor with. Measured against the XP a thorough run
# actually earns, not guessed. Re-measured after the XP table was reshaped:
# the early floors arrive a level or two lower than they used to, and the
# deep ones a good deal higher now that the curve runs to 25.
LEVEL_BY_FLOOR = {1: 1, 2: 3, 3: 5, 4: 7, 5: 9, 6: 10, 7: 11,
                  8: 14, 9: 16, 10: 18, 11: 20, 12: 22, 13: 25}


def _complete_floor(state, content, floor_n):
    """Mark everything on a floor as already found and beaten."""
    floor = content.floor(floor_n)
    state.flags[f"floor_cleared.{floor_n}"] = True
    state.flags[f"quirk_seen.{floor_n}"] = True

    for room_id, room in floor["rooms"].items():
        state.visited.append(room_id)
        for slot in ("boss", "miniboss", "elite"):
            if room.get(slot):
                state.flags[f"defeated.{room[slot]}"] = True
        for entry in room.get("contents", []):
            if entry.get("flag"):
                state.flags[entry["flag"]] = True
            if entry["type"] == "npc":
                state.flags[f"talked.{entry['id']}"] = True
                state.flags[f"gift.{entry['id']}"] = True
                if entry.get("minigame"):
                    state.flags[f"minigame_done.{entry['id']}"] = True
        for script in room.get("on_enter", []):
            if script.get("flag"):
                state.flags[script["flag"]] = True
        if room.get("kind") == "safe":
            state.flags[f"rested.{room_id}"] = True

    # Encounter monsters on that floor count as met.
    for entry in floor.get("encounter_table", []):
        state.flags[f"defeated.{entry['monster']}"] = True


def build(content, floor_n, name, class_id, companion_id, eggs):
    state, _ = step_mod.new_game(content, 20260806 + floor_n, name,
                                 class_id, companion_id)
    rng = Rng(state.seed, state.rng_counter)
    out = []

    # Level up to where this floor expects you.
    target = LEVEL_BY_FLOOR[floor_n]
    threshold = progression.xp_for_level(target)
    if threshold > 0:
        progression.award_xp(state, content, rng, threshold, out)

    # Everything above this floor is done.
    for done in range(1, floor_n):
        _complete_floor(state, content, done)
        loot_mod.give(state, content, rng, FLOOR_KEY_ITEMS.get(done, []), out)

    if eggs:
        state.flags["wall_secret_found"] = True
        state.flags["voices"] = True
        state.flags["withdraw_count"] = 13
        state.flags["sing_reward"] = True
        state.flags["photo_reward"] = True
        state.flags["photo_floors"] = list(range(1, min(floor_n, 5) + 1))
        loot_mod.give(state, content, rng, EGG_ITEMS, out)

    # Gear and consumables appropriate to the depth.
    weapon, armour = GEAR_BY_FLOOR.get(floor_n - 1, (None, None))
    if weapon:
        spec = content.item(weapon)
        stat = spec.get("stat", "str")
        # Only keep it if the class can actually swing it well.
        if state.player.mod(stat) < 2:
            weapon = CLASS_WEAPON.get(class_id, weapon)
        state.equipped["weapon"] = weapon
    if armour:
        state.equipped["armour"] = armour
    potion = next(item for depth, item in STOCK_BY_FLOOR if depth <= floor_n)
    for _ in range(4):
        state.add_item(potion)
    state.add_item("root_token")
    state.add_item("adrenaline")

    state.currency = 200 + floor_n * 180
    state.inventory_bonus = 2 if floor_n >= 5 else 0
    if floor_n > 9:
        state.continue_available = True

    # Drop in at the top of the floor.
    if floor_n > 1:
        step_mod.descend(state, content, rng, floor_n, out)
    state.rng_counter = rng.counter

    # Arrive rested, with everything available.
    state.player.hp = state.player.hp_max
    state.companion.alive = True
    state.companion.hp = state.companion.hp_max
    progression.restore_ability_uses(state, content)
    state.stalkers = []
    state.combat = None
    state.pending = None
    state.mode = "explore"
    state.visited = sorted(set(state.visited))
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("floors", nargs="*", type=int)
    parser.add_argument("--name", default="Bud")
    parser.add_argument("--class", dest="class_id", default="vanguard")
    parser.add_argument("--companion", default="grunk")
    parser.add_argument("--no-eggs", dest="eggs", action="store_false")
    parser.add_argument("--bundle", nargs="?", const="thirteen-saves.json",
                        help="also write one JSON of base64 saves, keyed by "
                             "path, for importing into the web wrapper")
    parser.add_argument("--bundle-prefix", default="/app/saves",
                        help="path prefix used for the keys in the bundle")
    args = parser.parse_args()

    content = load_from_disk(os.path.join(ROOT, "content"))
    wanted = args.floors or sorted(content.floors)
    os.makedirs(OUT_DIR, exist_ok=True)

    # Load screens list newest first, so stamp the saves in reverse: floor 1
    # gets the latest time and therefore sits at the top of the list.
    now = time.time()
    stamps = {n: now - (n * 60) for n in wanted}

    bundle = {}
    for floor_n in wanted:
        state = build(content, floor_n, args.name, args.class_id,
                      args.companion, args.eggs)
        text = saves.encode(state, saved_at=stamps[floor_n])
        name = f"floor-{floor_n:02d}.13save"
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        # Match the file's mtime to its stamp, for load screens that use it.
        os.utime(path, (stamps[floor_n], stamps[floor_n]))
        bundle[f"{args.bundle_prefix.rstrip('/')}/{name}"] = base64.b64encode(
            text.encode("utf-8")).decode("ascii")

        # Prove it loads before claiming it works.
        reloaded, warning = saves.load_state(text, content)
        assert reloaded.floor == floor_n, floor_n
        print(f"  {name}  "
              f"lvl {state.player.level:2}  "
              f"hp {state.player.hp}/{state.player.hp_max}  "
              f"{state.currency} clips  "
              f"{len(state.keepsakes)} keepsakes"
              + (f"  [{warning}]" if warning else ""))
    print(f"\n{len(wanted)} saves written to {OUT_DIR}")

    if args.bundle:
        bundle_path = args.bundle
        if not os.path.isabs(bundle_path):
            bundle_path = os.path.join(OUT_DIR, bundle_path)
        with open(bundle_path, "w", encoding="utf-8") as handle:
            json.dump(bundle, handle, indent=2)
        print(f"bundle written to {bundle_path} ({len(bundle)} saves)")


if __name__ == "__main__":
    main()
