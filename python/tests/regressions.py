"""Regression tests for things that actually went wrong in play.

    python3 tests/regressions.py

Each one is here because it shipped broken once. Bot playthroughs catch
crashes; these catch behaviour that is wrong but perfectly stable.
"""

import contextlib as _contextlib
import io as _io
import os
import json
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import actions, events as ev_mod, progression, saves, step as step_mod  # noqa: E402
from engine.content import load_from_disk                    # noqa: E402
from engine.rng import Rng                                   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def fresh(content, floor=1, cls="vanguard", comp="grunk", seed=4242):
    state, _ = step_mod.new_game(content, seed, "Test", cls, comp)
    if floor > 1:
        rng = Rng(state.seed, state.rng_counter)
        out = []
        step_mod.descend(state, content, rng, floor, out)
        state.rng_counter = rng.counter
    return state


def test_toll_charged_once(content):
    """The Floor 2 entry toll was charged twice per room: the whole room-entry
    block had been pasted in twice."""
    state = fresh(content, floor=2)
    state.currency = 500
    toll = content.floor(2)["quirk"]["entry_toll"]

    room = content.room(2, state.room)
    direction = list(room["exits"])[0]
    before = state.currency
    state, events = step_mod.step(state, actions.move(direction), content)
    charged = before - state.currency
    check("floor 2 toll charged once per room",
          charged == toll, f"charged {charged}, expected {toll}")

    entries = [e for e in events if e.kind == "RoomEntered"]
    check("room description printed once",
          len(entries) == 1, f"{len(entries)} RoomEntered events")


def test_toll_not_recharged(content):
    """Revisiting a room must not charge again."""
    state = fresh(content, floor=2)
    state.currency = 500
    room = content.room(2, state.room)
    direction = list(room["exits"])[0]
    state, _ = step_mod.step(state, actions.move(direction), content)
    back = {"north": "south", "south": "north",
            "east": "west", "west": "east"}[direction]
    state, _ = step_mod.step(state, actions.move(back), content)
    before = state.currency
    state, _ = step_mod.step(state, actions.move(direction), content)
    check("revisiting a room is free", state.currency == before,
          f"lost {before - state.currency} on a revisit")


def test_no_wasted_items(content):
    """Using a healing item at full health must refuse, not consume."""
    state = fresh(content)
    state.player.hp = state.player.hp_max
    had = state.has_item("ration")
    state, events = step_mod.step(state, actions.use("ration"), content)
    check("full-health heal is refused",
          state.has_item("ration") == had
          and any(e.kind == "Error" for e in events))


def test_full_pack_keeps_loot(content):
    """Loot that will not fit stays in the room instead of vanishing."""
    from engine import loot as loot_mod
    state = fresh(content)
    while not state.inventory_full():
        state.add_item("laminate", 1)
        state.inventory[-1]["id"] = f"pad{len(state.inventory)}"
        state.inventory[-1]["id"] = "own_merch"
        if len(state.inventory) > 30:
            break
        state.inventory.append({"id": "laminate", "qty": 1})
    out = []
    left = loot_mod.give(state, content, Rng(1, 0), ["adrenaline"], out,
                         stash_key="testchest")
    check("unfittable loot is stashed, not lost",
          left == ["adrenaline"]
          and state.flags.get("stash.testchest") == ["adrenaline"])


def test_save_roundtrip(content):
    state = fresh(content, floor=3)
    blob = saves.encode(state)
    reloaded, _ = saves.load_state(blob, content, allow_dead=True)
    check("save round-trips byte-identical",
          reloaded.to_dict() == state.to_dict())


def test_ability_progression(content):
    """Every class must gain something at each ability level."""
    for cls_id, cls in content.classes.items():
        levels = sorted(g["level"] for g in cls["abilities"])
        check(f"{cls_id} has abilities past level 3",
              max(levels) >= 9, f"tops out at level {max(levels)}")
        for grant in cls["abilities"]:
            check(f"{cls_id}:{grant['id']} is defined",
                  grant["id"] in content.abilities)


def test_companion_lifecycle(content):
    """Takes damage, mends as you walk, and stays down until a safe room."""
    from engine import combat as cmb

    # It should actually get hit sometimes. Measured rate is ~36%, but at
    # n=30 that is a coin flip on which seeds happen to line up: any engine
    # change that shifts the RNG stream re-rolls this test rather than
    # testing it. 120 seeds puts the floor well clear of the noise.
    hurt = 0
    trials = 120
    for seed in range(trials):
        state = fresh(content, seed=seed)
        state.flags["tut.combat"] = True
        state.player.hp_max = 400
        state.player.hp = 400
        rng = Rng(state.seed, state.rng_counter)
        cmb.start_combat(state, content, rng, ["queue", "queue"],
                         source_room=state.room)
        state.rng_counter = rng.counter
        for _ in range(40):
            if state.mode != "combat":
                break
            state, _ = step_mod.step(state, actions.attack(), content)
        if state.companion.hp < state.companion.hp_max:
            hurt += 1
    check("companion takes damage in combat", hurt >= trials // 5,
          f"hurt in {hurt}/{trials} fights")

    # Walking mends a living companion.
    state = fresh(content)
    state.companion.hp = 1
    room = content.room(1, state.room)
    direction = list(room["exits"])[0]
    state, _ = step_mod.step(state, actions.move(direction), content)
    check("living companion mends as you walk", state.companion.hp > 1,
          f"still on {state.companion.hp}")

    # A downed one does not mend, whatever you do.
    state = fresh(content)
    state.companion.hp = 0
    state.companion.alive = False
    for _ in range(3):
        room = content.room(state.floor, state.room)
        state, _ = step_mod.step(
            state, actions.move(list(room["exits"])[0]), content)
        if state.mode != "explore":
            break
    check("downed companion stays down while walking",
          not state.companion.alive and state.companion.hp == 0)

    # A safe room brings it back.
    state = fresh(content)
    state.companion.hp = 0
    state.companion.alive = False
    safe = next(rid for rid, r in content.floor(1)["rooms"].items()
                if r.get("kind") == "safe")
    state.room = safe
    state.visited.append(safe)
    state, _ = step_mod.step(state, actions.rest(), content)
    check("resting revives the companion",
          state.companion.alive
          and state.companion.hp == state.companion.hp_max)


def test_portrait_shows_description(content):
    """CHAR showed the one-word class verb instead of the description."""
    state = fresh(content, cls="vanguard")
    state, events = step_mod.step(state, actions.portrait(), content)
    portrait = next(e for e in events if e.kind == "Portrait")
    note = portrait.entries[0]["note"]
    check("char screen shows the full class description",
          len(note.split()) > 10, f"got {note!r}")
    check("char screen shows no separate verb line",
          "verb" not in portrait.entries[0])


def test_save_ordering(content):
    """'Continue' must find the newest save even when mtimes tie.

    Several Android filesystems round mtime to the nearest second or two, so
    saves written close together compare equal and the sort silently fell
    back to directory order, loading whatever listed first.
    """
    import tempfile
    import time as _time
    from frontends.terminal import main as fe

    with tempfile.TemporaryDirectory() as tmp:
        original, fe.SAVE_DIR = fe.SAVE_DIR, tmp
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            written = ["zulu", "alpha", "mike"]      # newest is "mike"
            for name in written:
                state = fresh(content)
                state.player.name = name
                with open(os.path.join(tmp, f"{name}.13save"), "w") as handle:
                    handle.write(saves.encode(state))
                _time.sleep(0.05)
            # Flatten every mtime, as a coarse filesystem would.
            stamp = _time.time()
            for name in written:
                os.utime(os.path.join(tmp, f"{name}.13save"), (stamp, stamp))

            newest = os.path.basename(fe.find_saves()[0])
            check("continue picks the newest save when mtimes tie",
                  newest == "mike.13save", f"picked {newest}")
        finally:
            os.chdir(cwd)
            fe.SAVE_DIR = original


def test_portrait_has_no_verb(content):
    """The char screen printed the one-word class verb above the description."""
    state = fresh(content, cls="vanguard")
    state, events = step_mod.step(state, actions.portrait(), content)
    portrait = next(e for e in events if e.kind == "Portrait")
    check("char screen no longer shows the bare class verb",
          "verb" not in portrait.entries[0])
    check("char screen still shows the description",
          len(portrait.entries[0]["note"].split()) > 10)


def test_dice_input(content):
    """Bids are two words; only the first was reaching the minigame, so every
    bid was rejected and the Floor 2 dice game could not be finished."""
    from frontends.terminal.main import parse
    state = fresh(content, floor=2)
    state.room = "02-r06"
    state.visited.append("02-r06")
    state, _ = step_mod.step(state, actions.talk(), content)
    check("talking to the diver starts the dice game", state.mode == "minigame")

    action = parse("3 4", state, content)
    check("a two-word bid survives parsing", action.arg == "3 4",
          f"got {action.arg!r}")
    state, events = step_mod.step(state, action, content)
    check("the bid is accepted",
          not any(e.kind == "Error" for e in events))


def test_equip_swaps_with_bag(content):
    """Equipped gear must not also sit in the bag, or it can be dropped while
    still equipped."""
    state = fresh(content, cls="vanguard")
    state.add_item("mop")
    state, _ = step_mod.step(state, actions.equip("mop"), content)
    check("equipping takes the item out of the bag",
          not state.has_item("mop") and state.equipped["weapon"] == "mop")
    check("the old weapon goes back in the bag", state.has_item("fire_axe"))

    state, events = step_mod.step(state, actions.drop("mop"), content)
    check("an equipped item cannot be dropped",
          state.equipped.get("weapon") == "mop"
          and any(e.kind == "Error" for e in events))


def test_key_items_cannot_be_dropped(content):
    state = fresh(content)
    state.add_item("audit_trail")
    state, events = step_mod.step(state, actions.drop("audit_trail"), content)
    check("key items cannot be dropped",
          state.has_item("audit_trail")
          and any(e.kind == "Error" for e in events))


def test_wall_secret(content):
    """Three goes at the same wall opens a room, once per run."""
    state = fresh(content)
    blocked = next(d for d in ("north", "south", "east", "west")
                   if d not in content.room(1, state.room).get("exits", {}))
    before = state.currency
    for _ in range(3):
        state, events = step_mod.step(state, actions.move(blocked[0]), content)
    check("three bumps opens the secret room",
          state.currency > before and state.has_item("persistence"))

    # Not repeatable, and interrupted attempts reset.
    again = state.currency
    for _ in range(3):
        state, _ = step_mod.step(state, actions.move(blocked[0]), content)
    check("the secret room only opens once", state.currency == again)


def test_class_style_text(content):
    """Every class needs its playstyle explained, not just named."""
    for cid, cls in content.classes.items():
        style = content.raw(f"classes.{cid}.style")
        check(f"{cid} has playstyle text", style is not None)
        if style:
            check(f"{cid} playstyle is a real explanation",
                  len(style.split()) > 25, f"only {len(style.split())} words")
        check(f"{cid} names its verb", content.raw(cls["verb_key"]) is not None)


def test_round_status(content):
    """Every round should report both sides, so the sheet is never needed."""
    from engine import combat as cmb
    from engine.rng import Rng

    state = fresh(content)
    state.flags["tut.combat"] = True
    rng = Rng(4, 0)
    events = cmb.start_combat(state, content, rng, ["intern", "signage"],
                              source_room=state.room)
    rounds = [e for e in events if e.kind == "RoundStarted"]
    check("a status block is emitted at the start of a fight", bool(rounds))
    if not rounds:
        return
    first = rounds[0]
    check("it reports the player", first.player
          and first.player["hp_max"] == state.player.hp_max)
    check("it reports the companion", first.companion
          and first.companion["hp_max"] == state.companion.hp_max)
    check("it reports every enemy", len(first.enemies) == 2)

    state.rng_counter = rng.counter
    for _ in range(12):
        if state.mode != "combat":
            break
        state, events = step_mod.step(state, actions.attack(), content)
        later = [e for e in events if e.kind == "RoundStarted"]
        if later:
            check("later rounds still report both sides",
                  later[0].player is not None and later[0].enemies is not None)
            break


def _fill_bag(state):
    """Distinct items only: add_item stacks by id, so repeats never fill slots."""
    for iid in ("mop", "stapler", "laminate", "own_merch", "vending_coffee",
                "smoke_canister", "chargeback_scroll", "adrenaline",
                "cold_tea", "grease", "crowbar", "waders", "casserole",
                "root_token", "quench", "secateurs", "rack_plate",
                "gardening_gloves", "cold_backup", "tape_flail"):
        if state.inventory_full():
            break
        state.add_item(iid)


def test_key_items_are_not_carried(content):
    """Trophies and maps do not take a carrying slot."""
    from engine import loot as loot_mod
    state = fresh(content)
    before = len(state.inventory)
    out = []
    loot_mod.give(state, content, Rng(1, 0),
                  ["audit_trail", "greeter_badge"], out)
    check("key items take no slots", len(state.inventory) == before,
          f"{len(state.inventory)} vs {before}")
    check("key items are recorded",
          state.keepsakes == ["audit_trail", "greeter_badge"])
    check("their effect still applies", state.flags.get("map_trail"))
    check("has_item still finds them", state.has_item("audit_trail"))

    out = []
    loot_mod.give(state, content, Rng(1, 0), ["audit_trail"], out)
    check("a duplicate key item is refused",
          state.keepsakes.count("audit_trail") == 1)


def test_enemy_drops_are_recoverable(content):
    """Loot from a kill must survive a full bag, not evaporate."""
    from engine import loot as loot_mod
    state = fresh(content)
    _fill_bag(state)
    check("bag is full for the test", state.inventory_full())

    out = []
    loot_mod.give(state, content, Rng(1, 0), ["ppe_vest"], out,
                  stash_key=f"room.{state.room}")
    check("unfittable kill loot is stashed in the room",
          state.flags.get(f"stash.room.{state.room}") == ["ppe_vest"])

    state, _ = step_mod.step(state, actions.drop("laminate"), content)
    state, _ = step_mod.step(state, actions.take(""), content)
    check("dropping something then TAKE recovers it",
          state.has_item("ppe_vest")
          and not state.flags.get(f"stash.room.{state.room}"))


def test_keepsake_migration(content):
    """Saves written before the split move key items out of the bag."""
    state = fresh(content)
    state.inventory.append({"id": "greeter_badge", "qty": 1})
    state.flags.pop("keepsakes_migrated", None)
    state, _ = step_mod.step(state, actions.look(), content)
    check("old key items migrate out of the bag",
          "greeter_badge" in state.keepsakes
          and not any(e["id"] == "greeter_badge" for e in state.inventory))


def test_minigame_hosts_are_named(content):
    """Both RPS hosts were labelled 'Ghost': the name was baked into the
    header string rather than coming from whoever was hosting."""
    def host_room(floor_n):
        return next(rid for rid, room in content.floor(floor_n)["rooms"].items()
                    for e in room.get("contents", []) if e.get("minigame"))

    hosts = [(1, "Ghost"), (4, "Postman")]
    for floor, expected in hosts:
        state = fresh(content, floor=floor)
        state.room = host_room(floor)
        state, events = step_mod.step(state, actions.talk(), content)
        prompts = [e for e in events if e.kind == "MinigamePrompt"]
        check(f"floor {floor} host starts a game", bool(prompts))
        if prompts:
            header = prompts[0].mg_state.splitlines()[0]
            check(f"floor {floor} host is named {expected}",
                  expected in header, f"header was {header!r}")


def test_art_lines_fit(content):
    """No art caption should be hand-wrapped onto a second line."""
    over = [key for key, art in content.art.items()
            if max((len(l) for l in art.split("\n")), default=0) > 44]
    check("all art fits the budget", not over, f"too wide: {over}")


def test_floor_narration_consistency(content):
    """Boss intros and floor-clear lines must name the right boss and clause.

    They used to fall back to a generic key that still had Floor 1 wording in
    it, so Floor 4 announced Mrs Hensley as The Greeter and reported Clause 1
    as disputed.
    """
    import re
    bosses = ["The Greeter", "Ledgermaw", "The Cap", "Mrs Hensley", "RAID-6",
              "The Likeness", "Kaleidon", "The Guarantor", "Hemikin",
              "Justice Vorn", "The Amendment"]
    for floor_n in sorted(content.floors):
        floor = content.floor(floor_n)
        boss_room = floor.get("boss_room")
        if not boss_room:
            continue
        boss_id = floor["rooms"][boss_room].get("boss")
        if not boss_id:
            continue          # Floor 13 ends in a conversation, not a boss
        name = content.t(content.monster(boss_id)["name_key"])
        clause = content.t(floor["name_key"])
        # One narrator, one track. The alternates are gone.
        for base, args in (("boss_intro", {"clause": clause, "boss": name}),
                           ("floor_cleared", {"clause": clause,
                                              "left": 13 - floor_n})):
            line = content.voice(f"{base}_{floor_n}") or content.voice(base)
            text = line.format(**args)
            wrong_boss = [b for b in bosses if b in text and b not in name]
            wrong_clause = [x for x in re.findall(r"Clause (\d+)", text)
                            if int(x) != floor_n]
            check(f"floor {floor_n} {base} is consistent",
                  not wrong_boss and not wrong_clause,
                  f"boss={wrong_boss} clause={wrong_clause}")


def test_big_beats_pause(content):
    """A boss fight and a floor change should not scroll past."""
    from engine.rng import Rng
    state = fresh(content, floor=1)
    out = []
    floor = content.floor(1)
    step_mod.enter_room(state, content, Rng(1, 0), floor["boss_room"], "south", out)
    check("there is a pause before the boss",
          any(e.kind == "Pause" for e in out))
    check("the boss is introduced before the pause",
          [e.kind for e in out].index("Narration")
          < [e.kind for e in out].index("Pause"))


def test_floor_clear_banner_is_per_floor(content):
    """One shared art file said CLAUSE 1 DISPUTED and all eleven floors used it."""
    check("the shared floor-clear art is gone",
          "floor_clear" not in content.art)
    for floor in content.floors.values():
        check(f"floor {floor['id']} has no hardcoded clear art",
              "clear_art" not in floor)


def test_finale_paths(content):
    """All three endings reachable, and withdrawal only with the original."""
    from engine.rng import Rng

    # Without the unamended term there is no withdrawal option.
    state = fresh(content, floor=13)
    out = []
    step_mod.enter_room(state, content, Rng(3, 0), "13-r40", "south", out)
    check("the finale opens as a conversation", state.mode == "choice")
    check("no withdrawal without the original Clause 1",
          "withdraw" not in state.pending["options"])

    # With it, the option appears and ends the run.
    state = fresh(content, floor=13)
    state.keepsakes.append("unamended_term")
    out = []
    step_mod.enter_room(state, content, Rng(3, 0), "13-r40", "south", out)
    check("carrying the original unlocks withdrawal",
          "withdraw" in state.pending["options"])
    state, events = step_mod.step(state, actions.Action("Choose", "withdraw"),
                                  content)
    # Withdrawing no longer ends it; see test_narrator_final_boss.
    check("withdrawing hands off to the secret fight",
          state.mode == "combat"
          and state.combat.enemies[0].monster_id == "the_narrator")

    # Refusing starts a fight instead.
    state = fresh(content, floor=13)
    out = []
    step_mod.enter_room(state, content, Rng(3, 0), "13-r40", "south", out)
    state, _ = step_mod.step(state, actions.Action("Choose", "refuse"), content)
    state, _ = step_mod.step(state, actions.Action("Choose", "yes"), content)
    check("refusing starts the fight", state.mode == "combat")


def _endgame(content, cls="advocate"):
    """A character as they would actually reach Floor 13: geared, near the cap.

    Level 13 and 300 HP was written against the old twenty-level table. Bot
    playthroughs put a thorough run at level 24-25 on Floor 13 with hit
    points at the 350 cap, so that is what the endgame tests fight with.
    """
    state = fresh(content, floor=13, cls=cls)
    state.player.level = 24
    state.player.hp_max = progression.HP_CAP
    state.player.hp = progression.HP_CAP
    state.player.stats.update({"str": 20, "dex": 16, "con": 18,
                               "int": 18, "cha": 22})
    state.equipped = {"weapon": "blank_page", "armour": "entire_plate"}
    state.flags["tut.combat"] = True
    return state


def test_narrator_final_boss(content):
    """Withdrawing does not end the run: the commentary objects."""
    from engine.rng import Rng
    state = _endgame(content)
    state.keepsakes.append("unamended_term")
    # Filed, so this exercises the flow at his written weight. The unfiled
    # penalty has its own test.
    state.keepsakes.append("notice_of_withdrawal")
    state.player.hp = 60          # arrive hurt; the fight should heal you
    out = []
    step_mod.enter_room(state, content, Rng(3, 0), "13-r40", "south", out)
    state, _ = step_mod.step(state, actions.Action("Choose", "withdraw"), content)

    check("withdrawing starts the secret fight", state.mode == "combat")
    check("the commentary is what you fight",
          state.combat.enemies[0].monster_id == "the_narrator")
    check("it heals you first so the fight is fair",
          state.player.hp == state.player.hp_max)

    # This test is about the flow — withdraw, fight, last choice — not about
    # whether attack-spam can out-attrition him, which is close to a coin
    # flip by design. Sit him on 1 HP so the win is deterministic and the
    # test cannot fail for a balance reason it is not measuring.
    state.combat.enemies[0].hp = 1
    for _ in range(120):
        if state.mode != "combat":
            break
        state, _ = step_mod.step(state, actions.attack(), content)
    check("beating it offers a last choice", state.mode == "choice",
          f"mode was {state.mode}")
    if state.mode != "choice":
        return
    state, _ = step_mod.step(state, actions.Action("Choose", "leave"), content)
    check("the run ends free", state.mode == "won"
          and state.flags.get("ending") == "free_leave")


def test_signed_ending_still_ends(content):
    """Signing must still finish immediately, with no secret boss."""
    from engine.rng import Rng
    state = fresh(content, floor=13)
    out = []
    step_mod.enter_room(state, content, Rng(3, 0), "13-r40", "south", out)
    state, _ = step_mod.step(state, actions.Action("Choose", "sign"), content)
    state, _ = step_mod.step(state, actions.Action("Choose", "yes"), content)
    check("signing ends the run there and then",
          state.mode == "won" and state.flags.get("ending") == "signed")


WITHDRAW_FLOORS = 13          # floors in the building


def test_easter_eggs(content):
    # One per clause, not thirteen anywhere: the count is of distinct floors.
    # Every memo frames it that way and the notice itself says so.
    state = fresh(content)
    for _ in range(13):
        state, _ = step_mod.step(state, actions.withdraw(), content)
    check("thirteen in one room files nothing",
          not state.has_item("notice_of_withdrawal"),
          state.flags.get("withdraw_count"))
    check("and only counts once", state.flags.get("withdraw_count") == 1,
          state.flags.get("withdraw_count"))

    state = fresh(content)
    for floor_n in range(1, 14):
        state.floor = floor_n
        state, _ = step_mod.step(state, actions.withdraw(), content)
    check("one on each of the thirteen floors is filed",
          state.has_item("notice_of_withdrawal"))

    # Miss a single clause and it is not filed.
    state = fresh(content)
    for floor_n in [n for n in range(1, 14) if n != 7]:
        state.floor = floor_n
        state, _ = step_mod.step(state, actions.withdraw(), content)
    check("missing one floor leaves it unfiled",
          not state.has_item("notice_of_withdrawal")
          and state.flags.get("withdraw_count") == WITHDRAW_FLOORS - 1,
          state.flags.get("withdraw_count"))

    # The target has to match the building, or it is unachievable.
    check("the target is one per floor",
          step_mod.WITHDRAW_TARGET == WITHDRAW_FLOORS,
          f"{step_mod.WITHDRAW_TARGET} vs {WITHDRAW_FLOORS} floors")

    state = fresh(content)
    safe = [rid for rid, r in content.floor(1)["rooms"].items()
            if r.get("kind") == "safe"]
    state.room = safe[0]
    state, _ = step_mod.step(state, actions.photo(), content)
    check("the photograph is in safe rooms",
          state.flags.get("photo_floors") == [1])
    state, events = step_mod.step(state, actions.photo(), content)
    check("looking twice in one room does not count",
          state.flags.get("photo_floors") == [1])

    state = fresh(content)
    state.room = "01-r01"
    state, events = step_mod.step(state, actions.sing(), content)
    check("singing outside a safe room does nothing",
          not state.flags.get("sang_in"))


def test_generated_saves(content):
    """The per-floor test saves must load, land in the right room, and leave
    the floor they drop you on completely untouched."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "make_saves", os.path.join(ROOT, "tools", "make_saves.py"))
    make_saves = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_saves)

    for floor_n in (1, 8, 13):
        state = make_saves.build(content, floor_n, "Test", "vanguard",
                                 "grunk", eggs=True)
        blob = saves.encode(state)
        loaded, _ = saves.load_state(blob, content)
        floor = content.floor(floor_n)
        check(f"floor {floor_n} save round-trips", loaded.to_dict() == state.to_dict())
        check(f"floor {floor_n} save starts in the right room",
              loaded.room == floor["start"], loaded.room)
        check(f"floor {floor_n} save is on the right floor",
              loaded.floor == floor_n)
        check(f"floor {floor_n} itself is untouched",
              not loaded.flags.get(f"floor_cleared.{floor_n}"))
        boss_room = floor["rooms"][floor["boss_room"]]
        boss = boss_room.get("boss") or boss_room.get("finale")
        check(f"floor {floor_n} boss is still alive",
              not loaded.flags.get(f"defeated.{boss}"))
        check(f"floor {floor_n} save arrives at full health",
              loaded.player.hp == loaded.player.hp_max)

    # The last one has to be able to reach the good ending.
    state = make_saves.build(content, 13, "Test", "advocate", "pip", eggs=True)
    check("the floor 13 save can withdraw",
          state.has_item("unamended_term"))


def test_rainbow_is_floor_seven_only(content):
    """Floor 7 paints its prose; every other floor is ordinary colour."""
    import io
    import contextlib
    import os as _os
    from engine.rng import Rng
    from frontends.terminal.render import Renderer

    _os.environ["TERM"] = "xterm-256color"
    expected = {n: ("rainbow" if n == 7 else "full") for n in content.floors}
    for floor_n, want in expected.items():
        check(f"floor {floor_n} palette is {want}",
              content.floor(floor_n).get("palette") == want,
              content.floor(floor_n).get("palette"))

    def hues(floor_n):
        state, events = step_mod.new_game(content, 7, "T", "vanguard", "grunk")
        out = []
        if floor_n > 1:
            step_mod.descend(state, content, Rng(state.seed, 0), floor_n, out)
            events = out
        renderer = Renderer(width=50, pace="fast")
        renderer.set_palette(state.palette)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            renderer.render([e for e in events if e.kind == "RoomEntered"])
        text = buf.getvalue()
        return len({p.split("m")[0] for p in text.split("\033[38;5;")[1:]})

    check("floor 6 room text is ordinary colour", hues(6) <= 1, hues(6))
    check("floor 7 room text runs a spectrum", hues(7) > 8, hues(7))
    check("floor 8 room text is back to plain", hues(8) <= 1, hues(8))


def test_resume_does_not_notify_rainbow(content):
    """Resuming on Floor 7 must stay terminal-only, same as arriving fresh.

    Floor 7's spectrum is a text effect; Floor 8's `colour_gag` is the browser
    overlay. A save loaded (or the page reloaded) while on Floor 7 shouldn't
    wake the Floor 8 overlay early.
    """
    from engine.rng import Rng
    state, _ = step_mod.new_game(content, 7, "T", "vanguard", "grunk")
    step_mod.descend(state, content, Rng(state.seed, 0), 7, [])
    events = step_mod.resume(state, content)
    palette_events = [e for e in events if e.kind == "PaletteChanged"]
    check("resume emits a palette event on floor 7", len(palette_events) == 1)
    check("but does not notify the browser overlay",
          not palette_events[0].get("notify", True))

    state8, _ = step_mod.new_game(content, 8, "T", "vanguard", "grunk")
    step_mod.descend(state8, content, Rng(state8.seed, 0), 8, [])
    events8 = step_mod.resume(state8, content)
    palette_events8 = [e for e in events8 if e.kind == "PaletteChanged"]
    check("floor 8's resume still notifies the browser",
          palette_events8 and palette_events8[0].get("notify", True))


def test_rainbow_starts_at_the_reveal(content):
    """The spectrum must begin on 'And it is in colour.', not before it."""
    import io
    import contextlib
    import os as _os
    from engine.rng import Rng
    from frontends.terminal.render import Renderer, RAINBOW_FROM

    _os.environ["TERM"] = "xterm-256color"
    arrival = content.raw("rooms7.r01.long")
    check("the arrival room carries the marker", RAINBOW_FROM in arrival)
    check("the marker sits on the reveal line",
          arrival.split(RAINBOW_FROM)[1].lstrip().startswith(
              "And it is in colour."))

    state, _ = step_mod.new_game(content, 7, "T", "vanguard", "grunk")
    out = []
    step_mod.descend(state, content, Rng(state.seed, 0), 7, out)
    renderer = Renderer(width=48, pace="fast")
    renderer.set_palette("full")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        renderer.render([e for e in out if e.kind == "RoomEntered"])
    text = buf.getvalue()
    check("the marker never reaches the screen", RAINBOW_FROM not in text)

    before, _, after = text.partition("And it is in colour")
    check("nothing before the reveal is painted",
          "\033[38;5;" not in before, "colour leaked above the reveal")
    check("everything after the reveal is painted", "\033[38;5;" in after)


def test_save_bundle(content):
    """The JSON bundle must decode to loadable saves, ordered floor 1 first."""
    import base64
    import json as _json
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle = os.path.join(tmp, "bundle.json")
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "make_saves.py"),
             "--name", "Test", "--bundle", bundle],
            capture_output=True, text=True)
        check("the generator runs", result.returncode == 0, result.stderr[-200:])
        if result.returncode != 0:
            return
        data = _json.load(open(bundle))
        check("the bundle holds every floor", len(data) == len(content.floors))
        check("keys use the wrapper's path form",
              all(k.startswith("/app/saves/floor-") and k.endswith(".13save")
                  for k in data))

        stamps = []
        for key, blob in sorted(data.items()):
            text = base64.b64decode(blob).decode("utf-8")
            state, _ = saves.load_state(text, content)
            floor_n = int(key.split("floor-")[1][:2])
            check(f"bundle floor {floor_n} loads in the right room",
                  state.floor == floor_n
                  and state.room == content.floor(floor_n)["start"])
            stamps.append(saves.written_at(text))

        check("floor 1 is stamped newest so it sorts first",
              stamps == sorted(stamps, reverse=True),
              "stamps are not descending by floor")


def test_new_minigames(content):
    """Hangman, the docket and the amended five-throw game all play."""
    from engine import minigames
    for game in ("rps", "dice", "hangman", "tictactoe", "amended"):
        check(f"{game} is registered", game in minigames.REGISTRY)

    # Looked up by host rather than by room id: the stalls and their
    # neighbours move around between floors now, so a hardcoded room number
    # quietly starts testing whoever moved in.
    def host_room(floor_n):
        return next(rid for rid, room in content.floor(floor_n)["rooms"].items()
                    for e in room.get("contents", []) if e.get("minigame"))

    plays = [(9, ["a", "e", "i", "o", "u"]),
             (10, None),                    # boxes chosen live; see below
             (11, ["clause", "waiver", "precedent", "schedule"])]
    for floor_n, moves in plays:
        state = fresh(content, floor=floor_n)
        state.room = host_room(floor_n)
        state, events = step_mod.step(state, actions.talk(), content)
        check(f"floor {floor_n} host starts a game", state.mode == "minigame",
              state.mode)
        if state.mode != "minigame":
            continue
        errors = 0
        for turn in range(len(moves) if moves else 6):
            if state.mode != "minigame":
                break
            if moves:
                move = moves[turn]
            else:
                # Play a box that is actually free, as a player would.
                board = state.minigame["board"]
                free = [str(i + 1) for i, cell in enumerate(board)
                        if cell == " "]
                if not free:
                    break
                move = free[0]
            state, events = step_mod.step(state, actions.minigame(move), content)
            errors += sum(1 for e in events if e.kind == "Error")
        check(f"floor {floor_n} accepts valid moves", errors == 0,
              f"{errors} rejected")


def test_browser_hook_fires(content):
    """Effects, floor clears and palette changes must reach the page.

    The bridge builds its dispatcher in JS and passes only JSON strings, so
    this fake only has to provide `eval` and the installed function.
    """
    import importlib
    import json as _json
    import sys as _sys
    import types

    seen = []
    fake_js = types.ModuleType("js")
    fake_js.document = object()          # not a worker

    def fake_eval(src):
        fake_js.__thirteenFire = lambda kind, payload: seen.append(
            (kind, _json.loads(payload)))
        return True

    fake_js.eval = fake_eval
    _sys.modules["js"] = fake_js
    _sys.modules["pyodide"] = types.ModuleType("pyodide")
    try:
        import frontends.terminal.effects as fx
        importlib.reload(fx)
        import frontends.terminal.render as rmod
        importlib.reload(rmod)

        renderer = rmod.Renderer(width=50, pace="fast")
        renderer.content = content
        from engine import events as ev

        renderer.render([ev.effect("storm")])
        # The still-frame path must NOT send :end, or the overlay is torn down
        # in the same tick it was raised. The overlay self-ends on `seconds`.
        check("storm dispatches a start and no immediate end",
              [k for k, _ in seen] == ["thirteen:effect"], [k for k, _ in seen])
        check("storm carries a usable duration",
              seen and seen[0][1].get("seconds", 0) >= 5,
              seen[0][1] if seen else None)
        check("detail carries the name as a plain key",
              seen and seen[0][1].get("name") == "storm")

        seen.clear()
        renderer.render([ev.floor_cleared(4, "Clause 4: Acceptable Use")])
        check("floor clear dispatches with its number",
              seen and seen[0][1].get("name") == "floor_cleared"
              and seen[0][1].get("floor") == 4)

        seen.clear()
        renderer.render([ev.palette_changed("rainbow")])
        check("a palette change dispatches",
              seen and seen[0][1].get("name") == "palette"
              and seen[0][1].get("palette") == "rainbow")

        check("the payload is JSON-serialisable end to end", fx.DISPATCHED > 0)
        check("no error was recorded", fx.LAST_ERROR is None, fx.LAST_ERROR)
    finally:
        for name in ("js", "pyodide", "pyodide.ffi"):
            _sys.modules.pop(name, None)
        importlib.reload(fx)
        importlib.reload(rmod)


def test_effects_degrade_in_browser(content):
    """Under Pyodide, animation must fall back to a still frame.

    `time.sleep` blocks the one JS thread and stdout only flushes when the
    call returns, so frames would arrive as a pile of cursor codes.
    """
    import importlib
    import sys as _sys
    import time as _time
    import types

    _sys.modules["pyodide"] = types.ModuleType("pyodide")
    try:
        import frontends.terminal.effects as fx
        import frontends.terminal.render as rmod
        importlib.reload(fx)
        importlib.reload(rmod)
        check("pyodide is detected", fx.IN_BROWSER)

        from engine import events as ev
        renderer = rmod.Renderer(width=50, pace="slow")
        renderer.content = content
        renderer.ansi = True
        buf = _io.StringIO()
        start = _time.time()
        with _contextlib.redirect_stdout(buf):
            renderer.render([ev.effect("storm"), ev.effect("party"),
                             ev.text_speed(90), ev.plain("slow text")])
        text = buf.getvalue()
        check("nothing blocks in the browser", _time.time() - start < 0.5)
        check("no cursor-movement codes are emitted",
              "\033[" not in text.replace("\033[0m", "").replace("\033[38", ""),
              "cursor codes leaked")
        check("a still frame is printed instead", len(text.strip()) > 40)
    finally:
        del _sys.modules["pyodide"]
        importlib.reload(fx)
        importlib.reload(rmod)


def test_effects_degrade(content):
    """Animations must vanish on a dumb terminal or `pace fast`, not hang."""
    import os as _os
    import time as _time
    from engine import events as ev
    from frontends.terminal.render import Renderer

    for term, pace in (("dumb", "slow"), ("xterm-256color", "fast")):
        _os.environ["TERM"] = term
        renderer = Renderer(width=50, pace=pace)
        start = _time.time()
        renderer.render([ev.effect("storm"), ev.effect("party"),
                         ev.text_speed(90), ev.plain("text")])
        check(f"effects skip on TERM={term} pace={pace}",
              _time.time() - start < 1.0)

    _os.environ["TERM"] = "dumb"
    renderer = Renderer(width=50, pace="slow")
    renderer.render([ev.effect("does_not_exist")])
    check("an unknown effect is ignored rather than crashing", True)


def test_floor_effects_and_pacing(content):
    check("floor 10 slows its text",
          content.floor(10).get("quirk", {}).get("slow_text", 0) > 0)
    check("floor 12 has an arrival effect",
          "storm" in step_mod.floor_effects(content.floor(12)),
          step_mod.floor_effects(content.floor(12)))
    check("floor 13 drains to white",
          step_mod.floor_effects(content.floor(13)) == ["blank"])
    from frontends.terminal.effects import Effects
    for floor_n in content.floors:
        for name in step_mod.floor_effects(content.floor(floor_n)):
            check(f"floor {floor_n} effect {name} exists",
                  hasattr(Effects, name))


def test_record_is_its_own_view(content):
    """The record used to sit inline on the sheet and crowded it out."""
    from engine import loot as loot_mod
    from engine.rng import Rng
    state = fresh(content)
    loot_mod.give(state, content, Rng(1, 0), ["audit_trail", "prism"], [])

    state, events = step_mod.step(state, actions.sheet(), content)
    sheet = next(e for e in events if e.kind == "Sheet")
    check("the sheet no longer inlines keepsakes",
          "keepsakes" not in sheet.payload)
    check("the sheet just counts them",
          sheet.payload.get("keepsake_count") == 2)

    state, events = step_mod.step(state, actions.record(), content)
    rec = next(e for e in events if e.kind == "Record")
    check("the record lists them in full", len(rec.entries) == 2)
    check("with their descriptions", all(e["desc"] for e in rec.entries))


def test_levels_reach_twenty(content):
    """The old thirteen-level cap was exhausted by Floor 6, leaving over half
    the game with no progression at all."""
    from engine import progression
    check("there are twenty levels", len(progression.XP_TABLE) == 20)

    running = 0
    reached = {}
    for floor_n in sorted(content.floors):
        floor = content.floor(floor_n)
        xp = sum(content.monster(r[s2])["xp"] * (2 if s2 == "elite" else 1)
                 for r in floor["rooms"].values()
                 for s2 in ("boss", "miniboss", "elite") if r.get(s2))
        table = floor["encounter_table"]
        avg = (sum(content.monster(e["monster"])["xp"] * e["weight"]
                   for e in table)
               / sum(e["weight"] for e in table))
        xp += int(avg * floor["encounter_chance"] * len(floor["rooms"]))
        running += xp
        level = 1
        while (progression.next_threshold(level)
               and running >= progression.next_threshold(level)):
            level += 1
        reached[floor_n] = level

    check("a thorough run is about level 11 by Floor 5",
          9 <= reached[5] <= 12, reached[5])
    check("and still levelling on Floor 10",
          reached[10] > reached[7], (reached[7], reached[10]))
    check("and reaches the cap only at the end",
          reached[13] >= 19, reached[13])

    for cid, cls in content.classes.items():
        levels = sorted(g["level"] for g in cls["abilities"])
        check(f"{cid} gains abilities in the last third",
              max(levels) >= 18, levels)


def test_carl_is_announced(content):
    """The bonus used to apply silently."""
    state, events = step_mod.new_game(content, 1, "Carl", "vanguard", "grunk")
    check("naming yourself Carl is flagged", state.flags.get("carl"))
    check("and it says so on the way in",
          any(e.kind == "Block" and "goodwill" in str(e.data.get("text", "")).lower()
              for e in events))
    state, events = step_mod.step(state, actions.record(), content)
    rec = next(e for e in events if e.kind == "Record")
    check("and again in the record", bool(rec.notes))

    plain, _ = step_mod.new_game(content, 1, "Bud", "vanguard", "grunk")
    check("other names get nothing", not plain.flags.get("carl"))


def test_wall_secret_is_broken_up(content):
    state = fresh(content)
    blocked = next(d for d in ("north", "south", "east", "west")
                   if d not in content.room(1, state.room).get("exits", {}))
    for _ in range(3):
        state, events = step_mod.step(state, actions.move(blocked[0]), content)
    check("the wall text has a stop in the middle",
          any(e.kind == "Pause" for e in events))


def test_no_unresolved_keys(content):
    """Twelve event strings shipped missing and printed as <<events.f12.ash>>.

    The validator only checked keys it knew about; this walks every *_key
    reference in the content and fires every random event.
    """
    from engine import quirks
    from engine.rng import Rng

    missing = []

    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("_key") and isinstance(value, str) and value:
                    if content.raw(value) is None:
                        missing.append(f"{where}:{key}={value}")
                else:
                    walk(value, where)
        elif isinstance(node, list):
            for item in node:
                walk(item, where)

    for label, blob in (("monsters", content.monsters), ("items", content.items),
                        ("abilities", content.abilities),
                        ("classes", content.classes),
                        ("companions", content.companions)):
        walk(blob, label)
    for floor_n in content.floors:
        walk(content.floor(floor_n), f"floor{floor_n}")
    check("every *_key reference resolves", not missing, missing[:4])

    unresolved = []
    for floor_n in sorted(content.floors):
        state = fresh(content, floor=floor_n) if floor_n == 1 else None
        if state is None:
            state = fresh(content)
            state.floor = floor_n
        for spec in content.floor(floor_n).get("random_events", []):
            out = []
            quirks._fire(state, content, Rng(7, 0), spec, out)
            for event in out:
                text = str(event.data.get("text", ""))
                if "<<" in text:
                    unresolved.append(f"floor {floor_n} {spec['id']}")
    check("every random event resolves its text", not unresolved,
          unresolved[:4])

    check("an empty key resolves to nothing", content.t("") == "")


def test_resume_reannounces_presentation(content):
    """Palette and the floor effect both re-sync on resume. Loading into any
    room on a floor should set the scene the same way arriving on it does:
    a burst of ENTRANCE_EFFECT_SECONDS, not a hold and not silence."""
    from engine.rng import Rng

    for floor_n, expect_effect in ((7, False), (12, True), (13, True)):
        # Arriving fresh: the burst fires at the start of the floor.
        state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
        out = []
        step_mod.descend(state, content, Rng(state.seed, 0), floor_n, out)
        fired = [e.name for e in out if e.kind == "Effect"]
        want = step_mod.floor_effects(content.floor(floor_n)) \
            if expect_effect else []
        check(f"floor {floor_n} fires on arrival", fired == want, fired)

        if expect_effect:
            secs = {e.get("seconds") for e in out if e.kind == "Effect"}
            # Not hardcoded: a floor may set `effect_seconds` to shorten its
            # overlay (Floor 13's blank runs 10s, not 30 — thirty seconds of
            # the page draining to white is a long time to read through).
            want_secs = step_mod.floor_effect_seconds(content.floor(floor_n))
            check(f"floor {floor_n} arrival burst runs for {want_secs:.0f}s",
                  secs == {want_secs}, secs)
            # And does not follow you around the floor.
            _, moved = step_mod.step(state, actions.move("s"), content)
            again = [e.name for e in moved if e.kind == "Effect"]
            check(f"floor {floor_n} does not re-fire on an ordinary move",
                  not again, again)

        fresh_state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
        step_mod.descend(fresh_state, content, Rng(fresh_state.seed, 0),
                         floor_n, [])
        blob = saves.encode(fresh_state)
        loaded, _ = saves.load_state(blob, content)
        events = step_mod.resume(loaded, content)
        kinds = [e.kind for e in events]
        check(f"floor {floor_n} resume re-announces the palette",
              "PaletteChanged" in kinds, kinds)
        check(f"floor {floor_n} resume re-announces text speed",
              "TextSpeed" in kinds, kinds)
        replayed = [e.name for e in events if e.kind == "Effect"]
        check(f"floor {floor_n} resume replays the effect immediately",
              replayed == want, replayed)

    # Floor 10's slow text must survive a reload too.
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    out = []
    step_mod.descend(state, content, Rng(state.seed, 0), 10, out)
    speeds = [e.cps for e in step_mod.resume(state, content)
              if e.kind == "TextSpeed"]
    check("floor 10 resume restores the slow text speed",
          speeds and speeds[0] > 0, speeds)


def test_ability_names_beat_the_verb_table(content):
    """Every ability must be reachable by typing its name in combat.

    "hit and run" was parsed as attack("and run") and "no case to answer"
    as a yes/no answer, so two abilities could never be used at all.
    """
    from frontends.terminal.main import parse, _match_ability
    from engine.state import MODE_COMBAT

    for class_id, aid, typed in (("skirmisher", "hit_and_run", "hit and run"),
                                 ("skirmisher", "cut_and_run", "cut and run"),
                                 ("advocate", "no_case", "no case to answer"),
                                 ("vanguard", "load_bearing", "load bearing")):
        state, _ = step_mod.new_game(content, 1, "T", class_id, "grunk")
        state.player.abilities = [a["id"] for a in
                                  content.classes[class_id]["abilities"]]
        state.mode = MODE_COMBAT
        action = parse(typed, state, content)
        check(f"'{typed}' is read as an ability",
              action is not None and action.kind == "Ability"
              and action.arg == aid,
              action and (action.kind, action.arg))

    # And the verbs those names start with still do their normal job.
    state, _ = step_mod.new_game(content, 1, "T", "skirmisher", "grunk")
    state.player.abilities = [a["id"] for a in
                              content.classes["skirmisher"]["abilities"]]
    state.mode = MODE_COMBAT
    action = parse("hit", state, content)
    check("a bare 'hit' still attacks",
          action is not None and action.kind == "Attack",
          action and action.kind)

    # Exact names win over another ability's loose word match.
    check("an exact name beats a word match",
          _match_ability("hit and run", state, content) == "hit_and_run")
    check("a single word still resolves to something",
          _match_ability("blindside", state, content) == "blindside")


def test_error_text_wraps_to_the_real_width(content):
    """Colour is applied after wrapping, not before.

    ANSI escapes are characters as far as textwrap is concerned, so
    colouring first cost nine columns and broke error messages early -
    "You cannot rest here. Things get in." split before the last word.
    """
    import io
    from contextlib import redirect_stdout
    from frontends.terminal.render import Renderer

    text = content.t("errors.not_safe")
    for width in (40, 44, 50, 60, 72):
        r = Renderer(content)
        r.width = width
        r.ansi = True
        r.colour = True
        r.pace = "fast"
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.wrap(text, colour="red")
        lines = [ln for ln in buf.getvalue().split("\n") if ln.strip()]
        plain = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in lines]
        longest = max((len(ln) for ln in plain), default=0)
        check(f"error text at width {width} uses the width it was given",
              len(text) > width or len(plain) == 1,
              (width, plain))
        check(f"error text at width {width} never overflows",
              longest <= width, (longest, plain))


def test_equipping_empties_the_slot(content):
    """Equipping removes the item from the bag, and a spare copy of what you
    are wearing is labelled as a spare rather than as the equipped one.

    The id comparison in _inventory_payload tagged any duplicate "equipped",
    which read as the armour never having left the bag at all.
    """
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    worn = state.equipped["armour"]

    state.add_item("ppe_vest")
    state, _ = step_mod.step(state, actions.equip("ppe_vest"), content)
    ids = [e["id"] for e in state.inventory]
    check("the newly equipped armour leaves the bag", "ppe_vest" not in ids, ids)
    check("and it is on the sheet", state.equipped["armour"] == "ppe_vest")
    check("the old armour comes back", worn in ids, ids)

    # A second copy of what is equipped is a spare, not the equipped one.
    state.add_item("ppe_vest")
    payload = step_mod._inventory_payload(state, content)
    spare = [i for i in payload if i["id"] == "ppe_vest"]
    check("a duplicate is in the payload", len(spare) == 1, payload)
    check("it is not labelled equipped", spare and not spare[0]["equipped"])
    check("it is labelled a spare", spare and spare[0]["spare"])


def test_trapdoor_drops_you_somewhere_else(content):
    """The Floor 9 trapdoor is a free one-way ride, not a punishment."""
    from engine.rng import Rng

    floor = content.floor(9)
    doors = [(rid, script) for rid, room in floor["rooms"].items()
             for script in room.get("on_enter", [])
             if script.get("event") == "teleport"]
    check("Floor 9 has a trapdoor", len(doors) == 1, doors)
    rid, script = doors[0]
    dest = script["to"]
    check("it goes somewhere real", dest in floor["rooms"], dest)
    check("and not to itself", dest != rid)
    check("the destination has no trapdoor of its own",
          not any(sc.get("event") == "teleport"
                  for sc in floor["rooms"][dest].get("on_enter", [])))
    check("nothing is stranded in the trapdoor room",
          not floor["rooms"][rid].get("contents"),
          floor["rooms"][rid].get("contents"))

    state = fresh(content, floor=9)
    state.room = "09-r08"
    hp_before = state.player.hp
    out = []
    step_mod.enter_room(state, content, Rng(1, 0), rid, "west", out)
    check("you end up in the destination", state.room == dest, state.room)
    check("it costs nothing", state.player.hp == hp_before,
          (hp_before, state.player.hp))
    check("it is counted", state.stats.trapdoors_fallen == 1)
    check("the destination room was actually described",
          any(e.kind == "RoomEntered" and e.room_id == dest for e in out),
          [e.kind for e in out])
    check("and no fight was started by the fall",
          not any(e.kind == "CombatStarted" for e in out))


def test_read_is_the_verb_for_walls(content):
    """READ, because TAKE is a strange thing to say to graffiti.

    TAKE still works on a note - it should not be the one thing TAKE refuses
    in a room you are looting - but READ only ever reads, so READ in a room
    with a chest in it does not quietly open the chest.
    """
    from frontends.terminal.main import parse

    floor = content.floor(9)
    note_room = next(rid for rid, room in floor["rooms"].items()
                     for e in room.get("contents", []) if e["type"] == "note")
    chest_room = next(rid for rid, room in floor["rooms"].items()
                      for e in room.get("contents", []) if e["type"] == "chest")

    state = fresh(content, floor=9)
    check("READ parses", parse("read", state, content).kind == "Read",
          parse("read", state, content))
    check("so does INSPECT", parse("inspect", state, content).kind == "Read")

    state.room = note_room
    state, events = step_mod.step(state, actions.read(), content)
    check("READ reads the wall", any(e.kind == "Memo" for e in events),
          [e.kind for e in events])
    check("and files it", state.stats.notes_read == 1, state.stats.notes_read)

    _, events = step_mod.step(state, actions.read(), content)
    check("reading it twice says so, and points at MEMOS",
          any(e.kind == "Error" for e in events), [e.kind for e in events])

    # READ must not be a second looting verb.
    state.room = chest_room
    before = list(state.inventory)
    _, events = step_mod.step(state, actions.read(), content)
    check("READ in a room with only a chest reads nothing",
          any(e.kind == "Error" for e in events), [e.kind for e in events])
    check("and does not open the chest", state.inventory == before,
          state.inventory)

    # TAKE still picks notes up, for anybody in the habit.
    state = fresh(content, floor=9)
    state.room = note_room
    state, events = step_mod.step(state, actions.take(""), content)
    check("TAKE still reads a note", any(e.kind == "Memo" for e in events),
          [e.kind for e in events])
    check("and files it exactly once",
          state.flags["notes"].count(
              next(e["text_key"] for e in floor["rooms"][note_room]["contents"]
                   if e["type"] == "note")) == 1,
          state.flags.get("notes"))


def test_notes_are_readable_and_kept(content):
    """Reading a note files it in the record permanently."""
    from engine.rng import Rng

    state = fresh(content, floor=9)
    floor = content.floor(9)
    spot = next((rid, e) for rid, room in floor["rooms"].items()
                for e in room.get("contents", []) if e["type"] == "note")
    rid, entry = spot
    state.room = rid

    out = []
    step_mod._take(state, content, Rng(1, 0), floor["rooms"][rid], "", out)
    check("the note is read", state.flags.get(entry["flag"]) is True)
    check("and filed", entry["text_key"] in state.flags.get("notes", []),
          state.flags.get("notes"))
    check("and counted", state.stats.notes_read == 1)

    # Memos have their own menu: twenty-seven of them buried the keepsakes
    # the record exists to show.
    _, events = step_mod.step(state, actions.record(), content)
    rec = [e for e in events if e.kind == "Record"]
    check("the record does not carry memos", rec and not rec[0].notes, rec)

    _, events = step_mod.step(state, actions.memos(), content)
    listing = [e for e in events if e.kind == "MemoList"]
    check("MEMOS lists it", listing and len(listing[0].entries) == 1, listing)

    _, events = step_mod.step(state, actions.memos("1"), content)
    memos = [e for e in events if e.kind == "Memo"]
    check("MEMOS 1 reads it back", memos, [e.kind for e in events])
    check("and a re-read is not marked fresh", memos and not memos[0].fresh)

    _, events = step_mod.step(state, actions.memos("9"), content)
    check("an out of range number is an error",
          any(e.kind == "Error" for e in events), [e.kind for e in events])

    # Reading it again must not duplicate the entry.
    step_mod._take(state, content, Rng(1, 0), floor["rooms"][rid], "", out)
    check("re-reading does not duplicate it",
          state.flags["notes"].count(entry["text_key"]) == 1,
          state.flags["notes"])


def test_memo_text_reflows(content):
    """Memos are prose, not layout: no hand-made line breaks, and green the
    first time only."""
    import io
    from contextlib import redirect_stdout
    from frontends.terminal.render import Renderer

    raw = content.raw("notes")
    check("there are memos to check", raw, raw)
    for key, text in raw.items():
        for para in text.split("\n\n"):
            check(f"notes.{key} has no hand-broken lines",
                  "\n" not in para.strip(), repr(para[:60]))
            check(f"notes.{key} has no hanging indent",
                  not para.startswith(" "), repr(para[:40]))

    sample = content.t(f"notes.{sorted(raw)[0]}")
    for width in (36, 52, 80):
        r = Renderer()
        r.content, r.pace, r.ansi, r.colour, r.width = content, "fast", False, False, width
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.render([ev_mod.memo(sample, fresh=True)])
        lines = [ln for ln in buf.getvalue().split("\n") if ln.strip()]
        check(f"memo fits width {width}",
              all(len(ln) <= width for ln in lines),
              max((len(ln) for ln in lines), default=0))
        check(f"memo is left aligned at {width}",
              all(not ln.startswith(" ") for ln in lines), lines[:2])

    # Green on the first read, plain on a re-read.
    def paint(fresh):
        r = Renderer()
        r.content, r.pace, r.ansi, r.colour, r.width = content, "fast", True, True, 60
        buf = io.StringIO()
        with redirect_stdout(buf):
            r.render([ev_mod.memo(sample, fresh=fresh)])
        return buf.getvalue()
    check("a newly found memo is green", "\033[38;5;114m" in paint(True))
    check("a re-read memo is not", "\033[38;5;114m" not in paint(False))


def test_carried_down_items_do_not_lie_about_where_they_are(content):
    """Chests reach two floors up for variety, and the text follows.

    A located found line describes a place, so off its own floor the item
    gets the carried-down line instead. That is what lets a Floor 4 casserole
    turn up on Floor 6 without claiming Floor 6 has doorsteps.
    """
    from engine import loot as loot_mod
    from engine.rng import Rng

    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    home = content.items["casserole"]["found_floor"]
    check("the casserole knows which floor it is from", home == 4, home)

    own = content.t("items.casserole.found")
    state.floor = home
    out = []
    loot_mod.give(state, content, Rng(3, 0), ["casserole"], out)
    notes = [e.note for e in out if e.kind == "ItemFound"]
    check("on its own floor it tells its own story", notes == [own], notes)
    state.remove_item("casserole")

    for floor in (home + 1, home + 2):
        state.floor = floor
        out = []
        loot_mod.give(state, content, Rng(3, 0), ["casserole"], out)
        notes = [e.note for e in out if e.kind == "ItemFound"]
        check(f"on floor {floor} it does not mention the doorstep",
              notes and notes[0] != own and "doorstep" not in notes[0],
              notes)
        check(f"on floor {floor} it says it was carried down",
              notes and notes[0] in content.raw("loot.carried_down"), notes)
        state.remove_item("casserole")

    # Every located item carries the marker, or the swap cannot happen.
    for iid, spec in content.items.items():
        if spec.get("found_key") and iid in (
                "casserole", "cold_backup", "quench", "good_tea"):
            check(f"{iid} declares its floor", spec.get("found_floor"), iid)

    # And the chest tables stay inside the two-floor window.
    for n in range(5, 14):
        for entry in content.loot[f"chest_f{n}_medical"]["entries"]:
            fl = content.items[entry["item"]].get("found_floor")
            check(f"F{n} medical: {entry['item']} is from this floor or "
                  f"up to two above", fl is not None and fl <= n <= fl + 2, fl)
        local = [e for e in content.loot[f"chest_f{n}_medical"]["entries"]
                 if content.items[e["item"]].get("found_floor") == n]
        check(f"F{n} medical still favours its own item",
              local and local[0]["weight"] > 50, local)


def test_healing_drops_belong_to_their_floor(content):
    """A random drop prints a found line, so it must be true where you are.

    A casserole says somebody left it on a doorstep. It dropped on Floors 4,
    5 and 6, and only Floor 4 has doorsteps. Buying is not finding - no found
    line is printed - so stock and vending tables are deliberately free.
    """
    import re
    from collections import defaultdict

    tiers = defaultdict(lambda: defaultdict(set))
    for name, table in content.loot.items():
        m = re.match(r"chest_f(\d+)_(common|good|hoard)$", name)
        if not m:
            continue
        for iid in (list(table.get("always", []))
                    + [e["item"] for e in table.get("entries", [])]):
            tiers[iid][int(m.group(1))].add(m.group(2))
    home = {}
    for iid, floors in tiers.items():
        full = sorted(n for n, seen in floors.items() if len(seen) == 3)
        if full:
            home[iid] = full[0]

    check("the casserole is a Floor 4 item", home.get("casserole") == 4,
          home.get("casserole"))
    check("cold backup is a Floor 5 item", home.get("cold_backup") == 5,
          home.get("cold_backup"))

    for name, table in content.loot.items():
        m = re.match(r"chest_f(\d+)(?:_(?:common|good|hoard|medical))?$", name)
        if not m:
            continue
        n = int(m.group(1))
        for iid in (list(table.get("always", []))
                    + [e["item"] for e in table.get("entries", [])]):
            spec = content.items[iid]
            if not spec.get("found_key"):
                continue
            where = content.items[iid].get("found_floor") or home.get(iid)
            span = 2 if name.endswith("_medical") else 0
            check(f"{name}: '{iid}' has a found line true on floor {n}",
                  where is None or where <= n <= where + span, where)

    # Every floor still drops something that heals, and its own thing.
    for n in range(5, 14):
        table = content.loot[f"chest_f{n}_medical"]
        items = [e["item"] for e in table["entries"]]
        check(f"Floor {n} medical chest is not empty", items, items)
        for iid in items:
            use = content.items[iid].get("use") or {}
            check(f"Floor {n} medical: {iid} heals",
                  use.get("op") in ("heal", "heal_full"), use)
            fl = content.items[iid].get("found_floor")
            check(f"Floor {n} medical: {iid} is from floor {n} or two above",
                  fl is not None and fl <= n <= fl + 2, fl)
            check(f"Floor {n} medical: {iid} is not a one-off prize",
                  not use.get("grants_flag")
                  and content.items[iid].get("price", 0) <= 300,
                  content.items[iid].get("price"))

    # And an item that drops must have something to say when it does.
    for n in range(2, 14):
        sig = [e["item"] for e in content.loot[f"chest_f{n}_medical"]["entries"]] \
            if f"chest_f{n}_medical" in content.loot else []
        for iid in sig:
            check(f"Floor {n}'s own drop has a found line",
                  content.items[iid].get("found_key"), iid)


def test_merchant_still_announced_on_a_revisit(content):
    """The one NPC you are meant to come back to has to stay visible.

    describe_room suppressed an NPC hint once talked.<id> was set, so after
    the first conversation the only sign the merchant was standing there
    disappeared.
    """
    from engine.rng import Rng

    state = fresh(content, floor=9)
    floor = content.floor(9)
    rid = next(rid for rid, room in floor["rooms"].items()
               for e in room.get("contents", [])
               if e["type"] == "npc" and e.get("shop") and e["id"] == "merchant")

    out = []
    step_mod.describe_room(state, content, Rng(1, 0), rid, True, out)
    first = " ".join(str(e.data.get("text", "")) for e in out)
    check("he is announced the first time", first.strip(), first)

    state.flags["talked.merchant"] = True
    out = []
    step_mod.describe_room(state, content, Rng(1, 0), rid, False, out)
    again = " ".join(str(e.data.get("text", "")) for e in out)
    check("and again on every revisit", again.strip(), again)
    check("with different words to the first time", again != first)


def test_seniors_are_a_fight_not_a_potion_treadmill(content):
    """A senior must be winnable without emptying the bag.

    Measured on a geared level 17 vanguard attacking every round, seniors on
    Floors 10 to 12 killed 25 runs out of 25. The cause was accuracy, not
    damage: at AC 22 the player connected about 40% of the time, so the fight
    ran 22 rounds while the senior out-damaged them by a hair the whole way.
    A senior is bigger, not harder to hit.
    """
    from engine import combat as combat_mod, progression
    from engine.rng import Rng
    from engine.state import MODE_COMBAT, MODE_DEAD

    # Asserted through spawn() rather than a module constant: the constant
    # was a no-op addition and got removed, and a test that only checks
    # `ELITE_AC == 0` would not have noticed if the addition came back.
    rng_ac = Rng(4242, 0)
    plain = combat_mod.spawn(content, rng_ac, "the_rider", elite=False)
    senior = combat_mod.spawn(content, rng_ac, "the_rider", elite=True)
    check("a senior gets no dodge bonus", senior.ac == plain.ac,
          f"{senior.ac} vs {plain.ac}")
    check("and a moderate health one", combat_mod.ELITE_HP <= 1.35,
          combat_mod.ELITE_HP)

    # The elite AC curve has to stay under what a late player can hit.
    for floor, mid in ((11, "the_rider"), (12, "the_liquidator"),
                       (13, "the_whole_agreement")):
        ac = combat_mod.spawn(content, rng_ac, mid, elite=True).ac
        check(f"floor {floor} senior AC is reachable", ac <= 20, ac)

    def fight(floor, mid, seed):
        state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
        progression.award_xp(state, content, Rng(1, 0), 40000, [])
        state.equipped["weapon"] = "blank_page"
        state.equipped["armour"] = "entire_plate"
        state.floor = floor
        for _ in range(25):
            state.add_item("whole_thing")
        combat_mod.start_combat(state, content, Rng(seed, 0), [mid], elite=True)
        heals = rounds = 0
        while state.mode == MODE_COMBAT and rounds < 300:
            rounds += 1
            if (state.player.hp < state.player.hp_max * 0.45
                    and state.has_item("whole_thing")):
                state, _ = step_mod.step(
                    state, actions.use("whole_thing"), content)
                heals += 1
            else:
                state, _ = step_mod.step(state, actions.attack(""), content)
            if state.mode == MODE_DEAD:
                break
        return heals, rounds, state.mode == MODE_DEAD

    import statistics
    for floor, mid, cap in ((10, "the_bailiff", 6), (11, "the_rider", 6),
                            (12, "the_liquidator", 10)):
        runs = [fight(floor, mid, seed) for seed in range(12)]
        heals = [r[0] for r in runs]
        rounds = [r[1] for r in runs]
        deaths = sum(r[2] for r in runs)
        check(f"F{floor} {mid}: nobody dies to it geared", deaths == 0, deaths)
        check(f"F{floor} {mid}: it does not eat the potion belt",
              statistics.median(heals) <= cap, statistics.median(heals))
        check(f"F{floor} {mid}: and it does not go on forever",
              statistics.median(rounds) <= 30, statistics.median(rounds))


def test_the_long_floors_have_somewhere_to_stop(content):
    """Break rooms have to be spread, not just counted.

    Adding a fourth room per floor was not enough on its own: placed by room
    number it landed one or two steps from the third, so Floors 12 and 13 had
    two break rooms effectively next door to each other while the long middle
    stretch had none. Spacing is measured by walking distance, not by
    position on the grid.
    """
    import collections

    def walk(rooms, start):
        seen = {start: 0}
        queue = collections.deque([start])
        while queue:
            rid = queue.popleft()
            for nxt in rooms[rid].get("exits", {}).values():
                if nxt in rooms and nxt not in seen:
                    seen[nxt] = seen[rid] + 1
                    queue.append(nxt)
        return seen

    for n in range(1, 14):
        floor = content.floor(n)
        rooms = floor["rooms"]
        safe = [rid for rid, r in rooms.items() if r.get("kind") == "safe"]
        want = 4 if n >= 7 else 2
        check(f"floor {n} has {want} break rooms", len(safe) == want, len(safe))

        # No two within three steps of each other.
        closest = min(min(walk(rooms, a).get(b, 99) for b in safe if b != a)
                      for a in safe)
        check(f"floor {n} break rooms are not on top of each other",
              closest >= 3, closest)

        # And nowhere on the floor is far from one.
        nearest = {rid: min(walk(rooms, s).get(rid, 99) for s in safe)
                   for rid in rooms}
        check(f"floor {n}: nowhere is more than 5 steps from a break room",
              max(nearest.values()) <= 5, max(nearest.values()))

        # One near the entrance, and one near the far end, rather than all
        # bunched at whichever end the room numbering favours.
        depth = walk(rooms, floor["start"])
        at = sorted(depth.get(rid, 99) for rid in safe)
        deepest = max(depth.values())
        check(f"floor {n} has an early refuge", at[0] <= 3, at)
        check(f"floor {n} has one in the second half",
              at[-1] >= deepest * 0.5, (at, deepest))


def test_map_heading_names_the_floor_first(content):
    """The heading used to read "FLOOR 11 - Full Disclosure", which looks
    like the floor is called Full Disclosure."""
    from engine import mapview

    state = fresh(content, floor=11)
    state.flags["map_full"] = True
    payload = mapview.build(state, content)
    check("the map carries the floor's own name",
          payload.floor_name == content.t(content.floor(11)["name_key"]),
          payload.floor_name)
    check("which is not the map tier", "Full Disclosure" not in payload.floor_name,
          payload.floor_name)


def test_map_numbers_only_the_last_nine_steps(content):
    """The legend says "1-9 steps back" and "o explored", so it has to mean it.

    The step label was min(9, index), which clamped rather than excluded:
    every room further back than nine steps was drawn as "9". On Full
    Disclosure, where the trail is the whole floor, that was a screen full
    of nines and almost no "o" at all.
    """
    import random
    from collections import Counter
    from engine import mapview

    for flag, name in (("map_trail", "Audit Trail"),
                       ("map_extended", "Extended Log"),
                       ("map_full", "Full Disclosure")):
        state = fresh(content, floor=10)
        state.flags[flag] = True
        floor = content.floor(10)
        random.seed(7)
        for _ in range(120):
            exits = list(floor["rooms"][state.room].get("exits", {}))
            if not exits:
                break
            state, _ = step_mod.step(
                state, actions.move(random.choice(exits)[0]), content)
            state.mode = "explore"
            state.minigame = None
            state.shop = None

        payload = mapview.build(state, content)
        check(f"{name}: the map draws", payload is not None)
        labels = Counter(cell["label"] for cell in payload.cells)
        digits = [k for k in labels if k.isdigit()]

        for d in digits:
            check(f"{name}: step {d} appears exactly once", labels[d] == 1,
                  labels[d])
        check(f"{name}: no step is numbered above nine",
              all(1 <= int(d) <= 9 for d in digits), sorted(digits))
        check(f"{name}: rooms further back read as explored",
              labels["o"] > 0, dict(labels))
        check(f"{name}: you are on the map exactly once", labels["@"] == 1,
              labels["@"])

    # Walking far enough that everything falls off the numbered trail leaves
    # numbers only near you, not scattered over the floor.
    state = fresh(content, floor=10)
    state.flags["map_full"] = True
    payload = mapview.build(state, content)
    labels = Counter(cell["label"] for cell in payload.cells)
    check("a fresh floor has no step numbers at all",
          not [k for k in labels if k.isdigit()], dict(labels))


def test_the_two_stalls_are_not_on_top_of_each_other(content):
    """The merchant and the machine were both in the top right corner of
    every late floor, because the placement script took the first free dead
    end and that was always the same room."""
    for n in range(5, 14):
        rooms = content.floor(n)["rooms"]
        spots = {}
        for rid, room in rooms.items():
            for e in room.get("contents", []):
                if e.get("id") in ("merchant", "vending_machine"):
                    spots[e["id"]] = room["pos"]
        check(f"floor {n} has both stalls", len(spots) == 2, spots)
        mx, my = spots["merchant"]
        vx, vy = spots["vending_machine"]
        gap = abs(mx - vx) + abs(my - vy)
        check(f"floor {n} keeps them apart", gap >= 5, gap)

    # And they are not in the same place on every floor.
    corners = {tuple(room["pos"])
               for n in range(5, 14)
               for room in content.floor(n)["rooms"].values()
               for e in room.get("contents", [])
               if e.get("id") == "vending_machine"}
    check("machines are not all in one spot", len(corners) >= 4, corners)


def test_late_stalls_sell_more_than_potions(content):
    """A duplicate draw used to consume the slot, so a table with its weight
    on two or three heals handed you a stall of nothing else."""
    from engine import shop as shop_mod
    from engine.rng import Rng

    def kind(iid):
        item = content.item(iid)
        use = item.get("use") or {}
        if item.get("slot"):
            return item["slot"]
        return "heal" if use.get("op") in ("heal", "heal_full") else "kit"

    for n in (9, 11, 13):
        kits = heals = runs = 0
        for seed in range(20):
            state, _ = step_mod.new_game(content, seed, "T", "vanguard", "grunk")
            state.floor = n
            stock = shop_mod._generate(state, content, Rng(seed, 0),
                                       {"stock_table": f"stock_f{n}",
                                        "slots": 6})
            ids = [row["item"] for row in stock]
            check(f"F{n} seed {seed}: no duplicate rows",
                  len(ids) == len(set(ids)), ids)
            check(f"F{n} seed {seed}: the shelf is not half empty",
                  len(ids) >= 6, len(ids))
            kinds = [kind(i) for i in ids]
            check(f"F{n} seed {seed}: something other than potions",
                  "kit" in kinds, kinds)
            # No armour on a stall any more: every floor's themed set is in
            # its own chests and off its senior, so selling it too spent
            # half a six-slot shelf on a thing you can wear one of.
            check(f"F{n} seed {seed}: nothing to wear on the shelf",
                  "armour" not in kinds, kinds)
            check(f"F{n} seed {seed}: two healing options at least",
                  kinds.count("heal") >= 2, kinds)
            kits += kinds.count("kit")
            heals += kinds.count("heal")
            runs += len(kinds)
        check(f"F{n}: kit is a real share of the shelf",
              kits / runs >= 0.2, round(kits / runs, 2))


def test_the_intro_still_stops_to_be_read(content):
    """The briefing and the pickers pause between sections.

    They print through wrap() and art() without going near an event, so a
    gate armed only inside render() latched off after the intro's first
    press and the remaining five briefing sections scrolled past in one go.
    The arming lives in the printing primitives now, which no new caller can
    bypass.
    """
    import builtins
    import io
    from contextlib import redirect_stdout
    from frontends.terminal import main as main_mod
    from frontends.terminal.render import Renderer

    def presses(run, width=50):
        marks = []
        buf = io.StringIO()

        def press(prompt=""):
            marks.append(buf.getvalue().count("\n"))
            return ""

        real_input = builtins.input
        builtins.input = press
        try:
            r = Renderer()
            r.content, r.pace, r.ansi, r.width = content, "slow", False, width
            with redirect_stdout(buf):
                run(r)
        finally:
            builtins.input = real_input
        gaps, prev = [], 0
        for m in marks:
            gaps.append(m - prev)
            prev = m
        return gaps, buf.getvalue().count("\n")

    gaps, total = presses(lambda r: main_mod._briefing(r, content))
    check("the briefing stops for every section", len(gaps) >= 5, len(gaps))
    check("and never twice with nothing between",
          all(g > 0 for g in gaps), gaps)
    check("and it is a real amount of reading", total > 60, total)

    # At `fast` nothing stops, which is the whole point of that setting.
    marks = []
    buf = io.StringIO()
    real_input = builtins.input
    builtins.input = lambda prompt="": marks.append(1) or ""
    try:
        r = Renderer()
        r.content, r.pace, r.ansi, r.width = content, "fast", False, 50
        with redirect_stdout(buf):
            main_mod._briefing(r, content)
    finally:
        builtins.input = real_input
    check("pace fast skips them all", not marks, len(marks))


def test_one_press_per_beat(content):
    """Presses are spaced, and none lands with nothing since the last.

    Two separate faults met here. A long narration gated itself and the
    explicit Pause after it gated again, so a floor clear asked twice with
    nothing in between. The guard added for that then latched: nothing ever
    marked "something has been printed", so after the first press every
    later Pause in the batch was swallowed and the finale played four beats
    as one wall. Counting presses is the wrong test - the property is the
    gap between them.
    """
    import builtins
    import io
    from contextlib import redirect_stdout
    from engine.rng import Rng
    from frontends.terminal.render import Renderer

    def run(build, width):
        marks = []
        buf = io.StringIO()

        def press(prompt=""):
            marks.append(buf.getvalue().count("\n"))
            return ""

        real_input = builtins.input
        builtins.input = press
        try:
            r = Renderer()
            r.content, r.pace, r.ansi, r.width = content, "slow", False, width
            with redirect_stdout(buf):
                r.render(build())
        finally:
            builtins.input = real_input
        gaps, prev = [], 0
        for m in marks:
            gaps.append(m - prev)
            prev = m
        return gaps, buf.getvalue().count("\n")

    def floor_clear():
        state, _ = step_mod.new_game(content, 4, "T", "vanguard", "grunk")
        state.floor = 7
        out = []
        step_mod._clear_floor(state, content, Rng(1, 0), out)
        return out

    def finale():
        state, _ = step_mod.new_game(content, 4, "T", "vanguard", "grunk")
        state.floor = 13
        out = []
        step_mod.describe_room(state, content, Rng(1, 0),
                               content.floor(13)["boss_room"], True, out)
        step_mod._finale_open(state, content, Rng(1, 0), out)
        return out

    for width in (50, 72):
        for label, build in (("floor clear", floor_clear), ("finale", finale)):
            gaps, total = run(build, width)
            check(f"{label} at {width}: it stops at all", gaps, gaps)
            check(f"{label} at {width}: no two presses in a row",
                  all(g > 0 for g in gaps), gaps)
            check(f"{label} at {width}: presses are spaced",
                  min(gaps) >= 3, gaps)
            check(f"{label} at {width}: not a press every paragraph",
                  total / len(gaps) >= 8, round(total / len(gaps), 1))

    # The finale is the one scene that earns several: the room, the thing in
    # it, the narrator, and the reveal, each on its own beat.
    gaps, _ = run(finale, 50)
    check("the finale is staged in beats", len(gaps) >= 4, len(gaps))

    # The descend block is prose, not layout, so it sits flush left.
    marks_buf = io.StringIO()
    r = Renderer()
    r.content, r.pace, r.ansi, r.width = content, "fast", False, 72
    with redirect_stdout(marks_buf):
        r.render(floor_clear())
    text = marks_buf.getvalue()
    check("the descend block is left aligned",
          "\nYou go down." in text, text[:80])
    check("and so is the clause under it",
          "\n  You agree" not in text, text[:200])


def test_indented_lines_wrap_aligned(content):
    """Combat lines are indented by convention. textwrap only saw the indent
    as part of the first line, so anything long came out crooked."""
    import io
    from contextlib import redirect_stdout
    from engine import events as ev_mod
    from frontends.terminal.render import Renderer

    r = Renderer()
    r.content, r.pace, r.ansi, r.width = content, "fast", False, 46
    line = content.t("combat.capped", was=40, cap=12)
    buf = io.StringIO()
    with redirect_stdout(buf):
        r.render([ev_mod.plain(line)])
    lines = [ln for ln in buf.getvalue().split("\n") if ln.strip()]
    check("the line was long enough to wrap", len(lines) > 1, lines)
    check("every wrapped line keeps the indent",
          all(ln.startswith("  ") for ln in lines), lines)
    check("and none overflow", all(len(ln) <= 46 for ln in lines),
          max(len(ln) for ln in lines))


def test_stalls_sell_what_runs_out(content):
    """No armour in a shop. Every floor's themed set is in that floor's own
    chests and comes off its senior, so a stall selling it too spent half a
    six-slot shelf on a thing you can wear one of.
    """
    from engine import shop as shop_mod
    from engine.rng import Rng

    armour = {i for i, v in content.items.items() if v.get("slot") == "armour"}

    for n in range(2, 14):
        table = content.loot.get(f"stock_f{n}")
        if not table:
            continue
        listed = (list(table.get("always", []))
                  + [e["item"] for e in table.get("entries", [])])
        sold_armour = [i for i in listed if i in armour]
        check(f"F{n} stock carries no armour", not sold_armour, sold_armour)

        # And the floor still hands the armour out some other way.
        from_floor = set()
        for name, other in content.loot.items():
            if not name.startswith((f"chest_f{n}", f"f{n}_")):
                continue
            from_floor |= {i for i in (list(other.get("always", []))
                                       + [e["item"] for e in other.get("entries", [])])
                           if i in armour}
        check(f"F{n} armour is still findable on the floor", from_floor,
              sorted(from_floor))

    # What a shelf actually looks like: potions and kit, nothing to wear.
    for n in (5, 9, 11, 13):
        for seed in range(6):
            state, _ = step_mod.new_game(content, seed, "T", "vanguard", "grunk")
            state.floor = n
            state.flags["map_trail"] = True
            state.flags["map_extended"] = True
            stock = shop_mod.open_shop(
                state, content, Rng(seed, 0),
                {"npc": "merchant", "stock_table": f"stock_f{n}", "slots": 6})
            items = [row["item"] for row in stock]
            check(f"F{n} shelf {seed}: no armour",
                  not [i for i in items if i in armour], items)
            heals = [i for i in items
                     if (content.items[i].get("use") or {}).get("op")
                     in ("heal", "heal_full")]
            check(f"F{n} shelf {seed}: at least two healing options",
                  len(heals) >= 2, heals)
            kit = [i for i in items
                   if not content.items[i].get("slot")
                   and (content.items[i].get("use") or {}).get("op")
                   not in (None, "heal", "heal_full")]
            check(f"F{n} shelf {seed}: something that is not a potion",
                  kit, items)
            check(f"F{n} shelf {seed}: nothing repeats",
                  len(set(items)) == len(items), items)

    # Selling armour to him still works: that is where a spare set goes.
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    state.floor = 11
    state.add_item("padded_ledger")
    shop_mod.open_shop(state, content, Rng(1, 0),
                       {"npc": "merchant", "stock_table": "stock_f11"})
    out = []
    shop_mod.sell(state, content, Rng(1, 0), "padded ledger", out)
    check("he still buys armour off you", state.currency > 0,
          state.currency)


def test_a_machine_never_speaks_as_the_merchant(content):
    """Every line a stall says has to belong to the stall saying it.

    The merchant's voice kept leaking into the vending machines: the
    farewell, the delivery line and the sold-out line were all hardcoded to
    him, so a machine said "I'll be one floor down. I usually am."
    """

    floor = content.floor(9)
    machine = next(e for room in floor["rooms"].values()
                   for e in room.get("contents", [])
                   if e.get("id") == "vending_machine")

    # TALK is the verb that opens it, so the intro has to say so.
    intro = content.t("npcs.vending_machine.intro").lower()
    check("the machine says it is voice operated", "voice operated" in intro,
          intro[:60])
    check("and points at the verb", "talk to it" in intro, intro[-90:])
    hint = " ".join(content.raw("rooms_common.vending_hint")).lower()
    check("the room hint says so too",
          "voice operated" in hint or "spoken to" in hint or "listens" in hint,
          hint)

    state = fresh(content, floor=9)
    state.currency = 900
    state.room = next(rid for rid, room in floor["rooms"].items()
                      for e in room.get("contents", [])
                      if e.get("id") == "vending_machine")

    merchant_lines = set(content.raw("shop.farewell")) \
        | set(content.raw("shop.sold_line")) \
        | set(content.raw("shop.bought_line")) \
        | set(content.raw("shop.upgrade_line"))

    state, events = step_mod.step(state, actions.talk(), content)
    state, events = step_mod.step(state, actions.buy("1"), content)
    said = [e.text for e in events if e.kind == "Speech"]
    who = [e.speaker for e in events if e.kind == "Speech"]
    check("the machine delivers in its own words",
          all(line not in merchant_lines for line in said), said)
    check("and under its own name",
          all(n == content.t("npcs.vending_machine.name") for n in who), who)

    state, events = step_mod.step(state, actions.leave_shop(), content)
    said = [e.text for e in events if e.kind == "Speech"]
    who = [e.speaker for e in events if e.kind == "Speech"]
    check("leaving does not get the merchant's goodbye",
          all(line not in merchant_lines for line in said), said)
    check("and is attributed to the machine",
          who == [content.t("npcs.vending_machine.name")], who)
    check("the machine's own farewells exist",
          said and said[0] in content.raw("shop.machine_farewell"), said)

    # The merchant still sounds like himself.
    state = fresh(content, floor=9)
    state.room = next(rid for rid, room in floor["rooms"].items()
                      for e in room.get("contents", [])
                      if e.get("id") == "merchant")
    state, _ = step_mod.step(state, actions.talk(), content)
    state, events = step_mod.step(state, actions.leave_shop(), content)
    said = [e.text for e in events if e.kind == "Speech"]
    check("the merchant keeps his own farewell",
          said and said[0] in content.raw("shop.farewell"), said)


def test_every_stall_has_its_own_stock(content):
    """Stock was keyed by floor alone, so two sellers on one floor shared it.

    A vending machine and the merchant on Floor 9 handed each other their
    stock and their sold-out flags, and whichever you reached first decided
    what the other one had.
    """
    from engine import shop as shop_mod
    from engine.rng import Rng

    floor = content.floor(9)
    stalls = [(rid, e) for rid, room in floor["rooms"].items()
              for e in room.get("contents", [])
              if e["type"] == "npc" and e.get("shop")]
    check("Floor 9 has two stalls", len(stalls) == 2, stalls)

    state = fresh(content, floor=9)
    state.currency = 500
    seen = {}
    for rid, entry in stalls:
        shop_mod.open_shop(state, content, Rng(4, 0), entry["config"])
        seen[entry["id"]] = [row["item"] for row in state.shop["stock"]]
    check("the two stalls stock different things",
          seen["merchant"] != seen["vending_machine"], seen)

    # A machine sells; it does not haggle, buy your things or fit a bag.
    machine = next(e for _r, e in stalls if e["id"] == "vending_machine")
    shop_mod.open_shop(state, content, Rng(4, 0), machine["config"])
    payload = shop_mod.payload(state, content)
    check("a machine offers no bag upgrade", payload["upgrade"] is None)
    check("and takes nothing off you", payload["sellable"] == [])
    out = []
    shop_mod.sell(state, content, Rng(1, 0), "ration", out)
    check("selling to a machine is refused",
          any(e.kind == "Error" for e in out), [e.kind for e in out])
    check("machine stock is all healing",
          all(content.item(row["item"]).get("use", {}).get("op") == "heal"
              for row in state.shop["stock"]), state.shop["stock"])

    # And the lines are attributed to whoever is actually there.
    check("the machine does not talk like the merchant",
          shop_mod.seller_name(state, content)
          == content.t("npcs.vending_machine.name"))


def test_tictactoe_can_actually_be_won(content):
    """The opponent is deterministic, so the fork has to work every time.

    Measured, a sensible player drew 100% of hands against it - it never
    misses a win or a block, and noughts and crosses going second is a draw
    against anything but one exact sequence. That sequence is written on a
    locker door on Floor 8, and this pins the two together: if the opponent
    changes, the memo becomes a lie.
    """
    from engine.rng import Rng
    from engine import minigames
    from engine.minigames.tictactoe import MAX_ROUNDS, ROUNDS_TO_WIN

    game = minigames.get("tictactoe")
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")

    hint = content.t("notes.f8_n3")
    check("the Floor 8 memo spells the sequence out",
          "one, eight, seven, nine" in hint, hint[-80:])

    for seed in (2, 17, 40):
        mg = game.start(state, content, Rng(seed, 0), {})
        rng = Rng(seed, 4)
        guard = 0
        while not game.result(mg) and guard < 40:
            guard += 1
            for box in ("1", "8", "7", "9"):
                if game.result(mg) or mg["board"] == [" "] * 9 and box != "1":
                    break
                mg, _ = game.step(state, content, rng, mg,
                                  actions.minigame(box))
                if mg["board"] == [" "] * 9:
                    break
        check(f"seed {seed}: the memo's sequence takes the match",
              game.result(mg) == {"won": True}, game.result(mg))
        check(f"seed {seed}: and it wins cleanly",
              mg["player_score"] == ROUNDS_TO_WIN and mg["opp_score"] == 0,
              (mg["player_score"], mg["opp_score"]))

    # A drawing player must not be stuck forever. Draws did not score, so a
    # player who simply blocked was in a match that could never end.
    def sensible(board):
        for mark in ("X", "O"):
            for i in range(9):
                if board[i] == " ":
                    trial = list(board)
                    trial[i] = mark
                    for a, b, c in minigames.get("tictactoe").LINES:
                        if trial[a] != " " and trial[a] == trial[b] == trial[c]:
                            return i
        return next(i for i in (4, 0, 2, 6, 8, 1, 3, 5, 7) if board[i] == " ")

    mg = game.start(state, content, Rng(9, 0), {})
    rng = Rng(9, 2)
    for _ in range(500):
        if game.result(mg):
            break
        mg, _ = game.step(state, content, rng, mg,
                          actions.minigame(str(sensible(mg["board"]) + 1)))
    check("a drawing player still reaches an end", game.result(mg) is not None,
          game.result(mg))
    check("and it takes no more than the round cap",
          mg["round"] <= MAX_ROUNDS, mg["round"])


def test_the_companion_is_accounted_for(content):
    """Where the companion comes from is answered before you pick one.

    They simply appeared: the game handed you a talking thing on the intake
    screen and never said why. It is clause 4(b) of the same agreement -
    parties in dispute may be accompanied, the arbitration will provide -
    and what it provided has been in the corridor longer than you have been
    in the building.
    """
    briefing = content.t("help.briefing.companion").lower()
    check("the briefing cites the clause", "4(b)" in briefing, briefing[:80])
    check("and says the arbitration provided",
          "arbitration will provide" in briefing or "provided" in briefing,
          briefing[:120])
    check("and that nobody asked them",
          "wanted the job" in briefing, briefing[-120:])

    intake = content.t("rooms.r01.long").lower()
    check("the form has the box", "4(b)" in intake, intake[:400])
    check("the name on it is yours to write",
          "your handwriting, and that one you did do" in intake, intake[:600])
    check("and they are visible through the door",
          "corridor" in intake, intake[:600])

    handover = content.t("companion.assigned", name="Grunk")
    check("the handover names them", "Grunk" in handover, handover[:80])
    check("and puts them outside, not beside you yet",
          "when you go through" in handover.lower(), handover[-140:])

    # It has to work for every companion, since the picker takes any of them.
    for cid, spec in content.companions.items():
        line = content.t("companion.assigned",
                         name=content.t(spec["name_key"]))
        check(f"the handover reads for {cid}",
              content.t(spec["name_key"]) in line, cid)


def test_the_disputants_add_up(content):
    """The story has to be consistent about who is where.

    It was not. The narrator said eleven disputed and the Signatory said ten
    signed and one turned back, which leaves the player unaccounted for; the
    standing line called you the eleventh; the ledger listed eleven names
    including yours. Meanwhile the mugs outside are warm forty years after
    the acquisition and the bosses have not moved in years.

    The canon, in one place:
      - eleven disputed BEFORE you; ten of them signed
      - the eleventh turned back on floor nine and is still in the building,
        leaving kettles on and the photograph up and the writing on the walls
      - you are the twelfth
      - a thirteenth is already inside, behind you, and the gaps are shortening
      - inside, no time passes: it has been the same afternoon since 1974,
        which is why nothing cools and nobody has moved
    """
    text = json.dumps(content.raw("dialogue")) + json.dumps(content.raw("narrator")) \
        + json.dumps(content.raw("standing")) + json.dumps(content.raw("notes"))
    low = text.lower()

    check("you are the twelfth, not the eleventh",
          "you are the twelfth" in low, "not stated")
    check("nothing still calls you the eleventh",
          "you are the eleventh" not in low, "still there")
    check("eleven disputed before you", "eleven disputed before you" in low)
    check("ten of them signed", "ten of them" in low)
    check("the eleventh turned back at nine",
          "turned round" in low and "back up" in low)
    check("and is still in the building", "still up there" in low)
    check("a thirteenth is behind you", "thirteenth" in low)

    # The frozen afternoon has to be stated somewhere a player will find it,
    # not only in the finale.
    memos = " ".join(content.t(f"notes.{k}")
                     for k in content.raw("notes")).lower()
    check("the memos explain why nothing cools",
          "same afternoon" in memos or "nothing perishes" in memos, "not hinted")
    check("and the Signatory explains it too",
          "same afternoon" in content.t("dialogue.signatory.eleven").lower(),
          content.t("dialogue.signatory.eleven")[:80])

    # The count in the standing line and the ledger.
    check("the standing line counts correctly",
          "twelfth" in content.t("standing.13").lower(),
          content.t("standing.13"))
    check("the ledger has a twelfth name",
          "twelfth" in content.t("items.ledger_page.desc").lower(),
          content.t("items.ledger_page.desc"))
    check("and the Signatory thanks twelve readers",
          "twelve of you read it" in content.t("dialogue.signatory.end_signed").lower(),
          content.t("dialogue.signatory.end_signed")[-80:])

    # The fled ending must not duplicate what the eleventh already does.
    fled = content.t("dialogue.narrator.fled_3").lower()
    check("the fled ending knows the photograph is already up",
          "already in every break room" in fled, fled[:120])
    check("and leaves something of its own", "sing" in fled, fled[-160:])

    # The agreement being arbitrated is made of real terms of service. The
    # jokes only work if they are still recognisably the real clause, so the
    # distinctive wording is pinned here rather than left to drift.
    memos_raw = " ".join(content.t(f"notes.{k}")
                         for k in content.raw("notes")).lower()
    for what, phrase in (("the soul clause", "immortal soul"),
                         ("the nuclear facilities clause", "nuclear facilities"),
                         ("the zombie clause", "reanimate"),
                         ("the community service clause", "community service"),
                         ("the firstborn clause", "first child born")):
        check(f"{what} is in the agreement somewhere", phrase in memos_raw,
              phrase)
    exhibit = content.t("rooms13.r29.long").lower()
    check("and the exhibit room shows what was asked for",
          "immortal soul" in exhibit and "first child" in exhibit,
          exhibit[:80])
    check("against what was given for it",
          "meal-kit" in exhibit, exhibit[:80])

    # The coffee you were made to leave at the intake door comes back at
    # every ending. It is the frozen afternoon in one object: still warm,
    # thirteen floors up, for as long as the building stands.
    opening = content.t("rooms.r01.long").lower()
    check("the intake room takes your coffee off you",
          "no food or drink" in opening and "coffee" in opening, opening[-160:])
    check("and it is warm when you put it down", "still warm" in opening,
          opening[-160:])
    for ending in ("dialogue.narrator.end_leave", "dialogue.narrator.end_take",
                   "dialogue.narrator.fled_4", "dialogue.signatory.end_signed"):
        check(f"{ending.split('.')[-1]} comes back to it",
              "coffee" in content.t(ending).lower(), ending)

    # The mugs belong to somebody the story can place.
    leave = content.t("dialogue.narrator.end_leave").lower()
    check("the mugs are explained", "two mugs" in leave, leave[-200:])
    check("and they are still warm for a reason",
          "everything in there is still warm" in leave, leave[-200:])
    check("their owner is not misplaced on floor nine",
          "is on floor nine" not in leave, leave[-200:])


def test_one_narrator_no_settings(content):
    """One narrator, always on. No voice ids, no mute.

    Picking a commentary track meant wondering about the other three for the
    rest of the run, and muting him removed most of the writing in the game.
    Both are gone, along with the items that unlocked voices.
    """
    from frontends.terminal.main import parse

    voices = content.raw("narrator.voices")
    check("there is exactly one narrator track", list(voices) == ["default"],
          list(voices))
    for gone in ("voice_list", "voice_locked", "voice_switch", "already_off",
                 "usage"):
        check(f"narrator.{gone} is gone",
              content.raw(f"narrator.{gone}") is None, gone)
    check("nothing unlocks a voice any more",
          not [i for i, v in content.items.items() if v.get("grants_voice")],
          [i for i, v in content.items.items() if v.get("grants_voice")])

    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    check("state carries no narrator settings",
          not hasattr(state, "narrator_on") and not hasattr(state, "narrator_voice"),
          [a for a in ("narrator_on", "narrator_voice") if hasattr(state, a)])
    check("NARRATOR is not a command any more",
          parse("narrator off", state, content) is None
          or parse("narrator off", state, content).kind != "Narrator",
          parse("narrator off", state, content))

    # He still speaks.
    out = []
    step_mod.narrate(state, content, Rng(1, 0), "voices_onset", out)
    check("and he is still narrating", any(e.kind == "Narration" for e in out),
          [e.kind for e in out])


def test_the_voices_have_enough_to_say(content):
    """The thing in the walls repeated itself fast, and looked like the
    narrator while doing it."""
    replies = content.raw("voices.reply")
    ambient = content.raw("voices.ambient")
    check("it has a real pool of replies", len(replies) >= 20, len(replies))
    check("and of ambient lines", len(ambient) >= 15, len(ambient))
    check("no duplicates in the replies",
          len(set(replies)) == len(replies), len(replies) - len(set(replies)))

    # It is flavour, not a hint system: the memos do hints.
    for line in replies + ambient:
        check("the voices do not give the withdrawal away",
              "withdraw" not in line.lower(), line[:60])

    # Purple, and not the narrator's grey.
    import io
    from contextlib import redirect_stdout
    from engine import events as ev_mod
    from frontends.terminal.render import Renderer

    r = Renderer()
    r.content, r.pace, r.ansi, r.colour, r.width = content, "fast", True, True, 60
    buf = io.StringIO()
    with redirect_stdout(buf):
        r.render([ev_mod.voice("test"), ev_mod.narration("test")])
    text = buf.getvalue()
    # Asserted against the palette rather than a literal escape, so the
    # colour can be retuned without editing the test — and against whichever
    # of the two purples this terminal gets, since _style falls back to the
    # 256-colour approximation without truecolour support.
    from frontends.terminal.render import C, TRUECOLOUR
    purple = C["purple"] if TRUECOLOUR else C["purple256"]
    check("the voice body is purple", purple in text, repr(text[:40]))
    check("the narrator is grey", C["grey"] in text, repr(text[-60:]))
    check("and they are different colours", purple != C["grey"]
          and text.count(purple) and text.count(C["grey"]))
    # Both speakers are labelled the same way, so neither reads as louder
    # than the other; only the body colour tells them apart.
    label = C["dim"]
    check("both labels use the narrator's styling",
          f"{label}[** Voice **]" in text and f"{label}[** Narrator **]" in text,
          repr(text[:40]))


def test_every_secret_is_hinted_somewhere(content):
    """Each undocumented thing has a memo pointing at it, with the right
    number in it. A hint with a wrong threshold is worse than no hint."""
    memos = {k: content.t(f"notes.{k}") for k in content.raw("notes")}
    all_text = "\n".join(memos.values()).lower()

    checks = [
        ("hidden stashes", "have a proper look round"),
        ("the walls that give", "three times"),
        ("the voices in an empty room", "something answers"),
        ("singing in break rooms", "three different ones"),
        ("the beach photograph", "fifth"),
        ("withdrawing", "thirteen of them, thirteen times"),
        ("naming yourself Carl", "give them carl"),
    ]
    for what, phrase in checks:
        check(f"{what} is hinted at", phrase in all_text, phrase)

    # And the numbers in those hints match the code.
    check("the wall really gives on the third bump",
          step_mod.WALL_BUMPS_FOR_SECRET == 3, step_mod.WALL_BUMPS_FOR_SECRET)
    check("withdrawing really takes thirteen",
          step_mod.WITHDRAW_TARGET == 13, step_mod.WITHDRAW_TARGET)
    check("singing really takes three rooms",
          step_mod.SING_TARGET == 3, step_mod.SING_TARGET)
    check("the photograph really takes five floors",
          step_mod.PHOTO_TARGET == 5, step_mod.PHOTO_TARGET)
    check("the voices really start on the fifth try",
          step_mod.VOICES_THRESHOLD == 5, step_mod.VOICES_THRESHOLD)

    # The withdrawal unlocks three of the five endings and is the one thing
    # a player will never guess, so every floor carries a reminder rather
    # than the whole game resting on one memo on Floor 11.
    for n in range(1, 14):
        floor_memos = [content.t(e["text_key"]).lower()
                       for room in content.floor(n)["rooms"].values()
                       for e in room.get("contents", [])
                       if e["type"] == "note"]
        # It has to name the word. "Say it, thirteen times" only helps
        # somebody who already read the memo that said what "it" is.
        hinted = [m for m in floor_memos if "withdraw" in m]
        check(f"floor {n} reminds you to withdraw", hinted, len(floor_memos))

    # The early hints have to be early, or the habits form too late to help.
    for key, floor in (("f1_n1", 1), ("f1_n2", 1), ("f2_n1", 2),
                       ("f3_n1", 3), ("f4_n1", 4)):
        placed = [rid for rid, room in content.floor(floor)["rooms"].items()
                  for e in room.get("contents", [])
                  if e.get("text_key") == f"notes.{key}"]
        check(f"{key} is on floor {floor}", placed, placed)

    # The merchant memo claims he is on every floor from 2 down.
    for n in range(2, 14):
        found = [rid for rid, room in content.floor(n)["rooms"].items()
                 for e in room.get("contents", []) if e.get("id") == "merchant"]
        check(f"the merchant really is on floor {n}", found, n)


def test_stashes_are_silent_until_you_look(content):
    """Paperclip stashes announce nothing. TAKE in a room that looks empty is
    the only way they are found, which is the whole point of them."""
    from engine.rng import Rng

    total = 0
    for n in range(1, 14):
        floor = content.floor(n)
        spots = [(rid, e) for rid, room in floor["rooms"].items()
                 for e in room.get("contents", []) if e["type"] == "stash"]
        check(f"floor {n} hides something", spots, n)
        total += len(spots)
        for rid, entry in spots:
            check(f"floor {n} {rid}: the stash has a flag", entry.get("flag"))
            check(f"floor {n} {rid}: and no hint of any kind",
                  not entry.get("hint_key"), entry)
    check("there are stashes worth hunting for", total >= 20, total)

    # Entering says nothing; TAKE pays; TAKE again does not.
    state = fresh(content, floor=9)
    floor = content.floor(9)
    rid, _entry = next((rid, e) for rid, room in floor["rooms"].items()
                       for e in room.get("contents", []) if e["type"] == "stash")
    state.room = rid

    out = []
    step_mod.describe_room(state, content, Rng(1, 0), rid, True, out)
    said = " ".join(str(e.data.get("text", "")) for e in out).lower()
    check("entering the room gives nothing away",
          "paperclip" not in said and "stash" not in said, said[-80:])

    before = state.currency
    out = []
    step_mod._take(state, content, Rng(1, 0), floor["rooms"][rid], "", out)
    check("looking finds it", state.currency > before,
          (before, state.currency))
    check("and it is counted", state.stats.stashes_found == 1)

    after = state.currency
    out = []
    step_mod._take(state, content, Rng(1, 0), floor["rooms"][rid], "", out)
    check("it cannot be found twice", state.currency == after,
          (after, state.currency))


def test_memo_tips_are_true(content):
    """A memo that gives advice has to be right about the mechanic.

    A wrong tip is worse than no tip, so each claim is pinned to the code it
    describes rather than left as prose nobody checks.
    """
    from engine.minigames.blackjack import DEALER_STANDS
    from engine.minigames import dice as dice_mod

    blackjack = content.t("notes.f6_n3")
    check("the Floor 6 memo has the dealer's rule right",
          f"stops on {['', 'one', 'two', 'three'][0] or ''}seventeen" in blackjack
          or "seventeen" in blackjack, blackjack[-60:])
    check("and the dealer really does stand on 17", DEALER_STANDS == 17)

    liar = content.t("notes.f5_n3")
    check("the Floor 5 memo describes the real tell",
          "raise than call" in liar, liar[-80:])
    check("and he really does lean high",
          "expected = have +" in open(dice_mod.__file__).read())

    trapdoor = content.t("notes.f9_n2")
    floor = content.floor(9)
    doors = [rid for rid, room in floor["rooms"].items()
             for sc in room.get("on_enter", []) if sc.get("event") == "teleport"]
    check("the Floor 9 memo points at a trapdoor that exists", doors, doors)
    check("the memo describes it as a floor that is not a floor",
          "floor that is not a floor" in trapdoor, trapdoor[-90:])
    x, y = floor["rooms"][doors[0]]["pos"]
    check("and it really is on the west run", x == 0, (x, y))

    safe = content.t("notes.f12_n3")
    check("the Floor 12 memo says break rooms shake pursuit",
          "nothing follows you into one" in safe, safe[-70:])
    check("and safe rooms really do clear stalkers",
          "clear_stalkers" in open(step_mod.__file__).read())

    record = content.t("notes.f10_n2")
    check("the Floor 10 memo describes keepsakes correctly",
          "do not go in your bag" in record, record[-90:])


def test_blackjack_is_playable(content):
    """Twenty-one, best of three hands.

    Replaced `precedent`, which asked you to memorise a sequence that was
    still on the screen above you - in a terminal with scrollback that tests
    nothing. Blackjack survives being visible, which is the point.
    """
    from engine.rng import Rng
    from engine import minigames
    from engine.minigames.blackjack import value

    game = minigames.get("blackjack")
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")

    # Ace counting: 11 until it cannot be.
    check("two aces are 12, not 22", value(["AS", "AH"]) == 12,
          value(["AS", "AH"]))
    check("ace plus king is 21", value(["AS", "KH"]) == 21)
    check("a soft hand hardens rather than busting",
          value(["AS", "9H", "5D"]) == 15, value(["AS", "9H", "5D"]))
    check("face cards are ten", value(["QS", "JH"]) == 20)
    check("three aces and an eight is 21", value(["AS", "AH", "AD", "8C"]) == 21,
          value(["AS", "AH", "AD", "8C"]))

    mg = game.start(state, content, Rng(11, 0), {})
    check("both sides are dealt two", len(mg["player"]) == 2
          and len(mg["dealer"]) == 2, mg)
    check("from a real shoe", len(mg["shoe"]) == 48, len(mg["shoe"]))
    check("with no duplicate cards",
          len(set(mg["shoe"] + mg["player"] + mg["dealer"])) == 52)

    # A whole match, played to a decision, from several seeds.
    for seed in (3, 11, 29, 44, 57):
        mg = game.start(state, content, Rng(seed, 0), {})
        rng = Rng(seed, 7)
        for _ in range(200):
            if game.result(mg):
                break
            move = "hit" if value(mg["player"]) < 17 else "stand"
            mg, _ = game.step(state, content, rng, mg, actions.minigame(move))
        res = game.result(mg)
        check(f"seed {seed} reaches a decision", res is not None, res)
        check(f"seed {seed} ends at two hands won",
              mg["player_score"] == 2 or mg["opp_score"] == 2,
              (mg["player_score"], mg["opp_score"]))
        check(f"seed {seed} never leaves a bust hand standing",
              value(mg["player"]) <= 21 or res == {"won": False},
              value(mg["player"]))

    # The dealer plays to a rule, not to taste.
    for seed in range(1, 40):
        mg = game.start(state, content, Rng(seed, 0), {})
        rng = Rng(seed, 3)
        mg, _ = game.step(state, content, rng, mg, actions.minigame("stand"))
        # After the dealer's turn she is either bust or on 17+, unless a new
        # hand has already been dealt over the top of it.
        if not game.result(mg) and mg["stood"]:
            check(f"seed {seed}: dealer stood on 17 or better",
                  value(mg["dealer"]) >= 17, value(mg["dealer"]))

    # Garbage does not advance the hand.
    mg = game.start(state, content, Rng(5, 0), {})
    before = list(mg["player"])
    mg, events = game.step(state, content, Rng(5, 1), mg,
                           actions.minigame("banana"))
    check("bad input is an error", any(e.kind == "Error" for e in events),
          [e.kind for e in events])
    check("and the hand is untouched", mg["player"] == before)

    check("precedent is gone", "precedent" not in minigames.REGISTRY,
          sorted(minigames.REGISTRY))


def test_later_floors_are_worth_walking(content):
    """The floors grew and the things in them did not.

    Chest density had fallen from 0.27 a room on Floor 1 to 0.10 on the
    last five, and Floors 5, 6, 7, 8, 12 and 13 had no minigame at all.
    """
    from engine import minigames

    hosts_by_floor = {}
    for n in range(1, 14):
        floor = content.floor(n)
        rooms = floor["rooms"]
        contents = [e for room in rooms.values()
                    for e in room.get("contents", [])]
        chests = [e for e in contents if e["type"] == "chest"]
        density = len(chests) / len(rooms)
        check(f"Floor {n} has chests worth finding", density >= 0.15,
              round(density, 3))

        hosts = [e for e in contents if e.get("minigame")]
        hosts_by_floor[n] = [e["minigame"] for e in hosts]
        if n >= 5:
            check(f"Floor {n} has something to play", hosts, n)
            machines = [e for e in contents if e.get("id") == "vending_machine"]
            check(f"Floor {n} has a vending machine", len(machines) == 1,
                  machines)
            notes = [e for e in contents if e["type"] == "note"]
            check(f"Floor {n} has notes to find", len(notes) >= 3, len(notes))

    played = {g for games in hosts_by_floor.values() for g in games}
    check("every game in the registry is actually used",
          played == set(minigames.REGISTRY), (played, set(minigames.REGISTRY)))


def test_every_choice_prompt_accepts_its_own_options(content):
    """Every named-choice prompt in the game, walked to its ending.

    The same fault turned up twice: a prompt sets MODE_CHOICE with named
    options, and the parser only produced a Choose action for yes and no. The
    Signatory's ASK/SIGN/REFUSE went first, then the Narrator's LEAVE/TAKE -
    TAKE parsed as the room verb and LEAVE as "leave the shop". The parser is
    now keyed on the prompt having options at all, so a new one cannot be
    added without it working.
    """
    from frontends.terminal.main import parse, prompt_for
    from engine.rng import Rng
    from engine.state import MODE_COMBAT, MODE_WON

    def at_finale(term=False):
        state, _ = step_mod.new_game(content, 4, "T", "vanguard", "grunk")
        state.floor = 13
        state.room = content.floor(13)["boss_room"]
        if term:
            state.add_item("unamended_term")
        step_mod._finale_open(state, content, Rng(1, 0), [])
        state.player.hp_max = 99999
        state.player.hp = 99999
        return state

    def win(state):
        turns = 0
        while state.mode == MODE_COMBAT and turns < 200:
            turns += 1
            for enemy in state.combat.enemies:
                enemy.hp = 1
            state.player.hp = 99999
            state, _ = step_mod.step(state, actions.attack(""), content)
        return state

    # Whatever the prompt is, it names its own options and takes them.
    def usable(state, expect):
        options = (state.pending or {}).get("options")
        check(f"prompt offers {expect}", options == expect, options)
        check("and the prompt line says so",
              prompt_for(state).strip() == "[" + "/".join(expect) + "] >",
              prompt_for(state))
        for word in expect:
            act = parse(word, state, content)
            check(f"'{word}' is taken as a choice",
                  act is not None and act.kind == "Choose" and act.arg == word,
                  act and (act.kind, act.arg))

    # 1. Signed.
    state = at_finale()
    usable(state, ["ask", "sign", "refuse"])
    for word in ("sign", "yes"):
        state, _ = step_mod.step(state, parse(word, state, content), content)
    check("signing ends the run", state.mode == MODE_WON
          and state.flags.get("ending") == "signed", state.flags.get("ending"))

    # 2. Fought and won.
    state = at_finale()
    for word in ("refuse", "yes"):
        state, _ = step_mod.step(state, parse(word, state, content), content)
    state = win(state)
    check("fighting ends the run", state.mode == MODE_WON
          and state.flags.get("ending") == "fought", state.flags.get("ending"))

    # 3 and 4. Withdrew, beat him, then the last choice in the game.
    for word, ending in (("leave", "free_leave"), ("take", "free_take")):
        state = at_finale(term=True)
        usable(state, ["ask", "withdraw", "sign", "refuse"])
        state, _ = step_mod.step(state, parse("withdraw", state, content),
                                 content)
        state = win(state)
        usable(state, ["leave", "take"])
        state, _ = step_mod.step(state, parse(word, state, content), content)
        check(f"'{word}' ends the run", state.mode == MODE_WON
              and state.flags.get("ending") == ending,
              state.flags.get("ending"))

    # 5. Ran from him.
    state = at_finale(term=True)
    state, _ = step_mod.step(state, parse("withdraw", state, content), content)
    for _ in range(80):
        if state.mode != MODE_COMBAT:
            break
        state, _ = step_mod.step(state, actions.flee(), content)
    check("running ends the run", state.mode == MODE_WON
          and state.flags.get("ending") == "upstairs",
          state.flags.get("ending"))

    # Every ending is the end of Clause 13, so every ending gets the banner
    # and the floor_cleared effect in the browser. Only the fight did, because
    # only the fight went through _clear_floor.
    def ending_events(build):
        state = build()
        return state

    checks = []
    state = at_finale()
    for typed in ("sign", "yes"):
        state, events = step_mod.step(state, parse(typed, state, content),
                                      content)
    checks.append(("signed", events, "SIGNED"))

    state = at_finale()
    for typed in ("refuse", "yes"):
        state, events = step_mod.step(state, parse(typed, state, content),
                                      content)
    turns = 0
    while state.mode == MODE_COMBAT and turns < 200:
        turns += 1
        for enemy in state.combat.enemies:
            enemy.hp = 1
        state.player.hp = 99999
        state, events = step_mod.step(state, actions.attack(""), content)
    checks.append(("fought", events, "DISPUTED"))

    for word in ("leave", "take"):
        state = at_finale(term=True)
        state, _ = step_mod.step(state, parse("withdraw", state, content),
                                 content)
        state = win(state)
        state, events = step_mod.step(state, parse(word, state, content),
                                      content)
        checks.append((f"free_{word}", events, "CLOSED"))

    state = at_finale(term=True)
    state, _ = step_mod.step(state, parse("withdraw", state, content), content)
    for _ in range(80):
        if state.mode != MODE_COMBAT:
            break
        state, events = step_mod.step(state, actions.flee(), content)
    checks.append(("upstairs", events, "ADJOURNED"))

    for name, events, verb in checks:
        banners = [e for e in events if e.kind == "FloorCleared"]
        check(f"the {name} ending gets the Clause 13 banner", banners,
              [e.kind for e in events])
        check(f"the {name} banner is on floor 13",
              banners and banners[0].floor == 13,
              banners and banners[0].floor)
        check(f"the {name} banner reads {verb}",
              banners and banners[0].get("verb") == verb,
              banners and banners[0].get("verb"))
        check(f"the {name} ending still ends the run",
              any(e.kind == "RunEnded" for e in events),
              [e.kind for e in events])

    # And the death prompt, which really is yes/no, is untouched by all this.
    state, _ = step_mod.new_game(content, 4, "T", "vanguard", "grunk")
    state.continue_available = True
    state.player.hp = 0
    step_mod._handle_death(state, content, Rng(1, 0), [])
    usable(state, ["yes", "no"])
    state, _ = step_mod.step(state, parse("yes", state, content), content)
    check("a reprieve puts you back on your feet",
          state.mode == "explore" and state.player.hp > 0,
          (state.mode, state.player.hp))

    # Every ending has a heading rather than falling back to RUN ENDED.
    src = open(os.path.join(ROOT, "frontends", "terminal", "render.py")).read()
    for ending in ("signed", "fought", "free_leave", "free_take", "upstairs"):
        check(f"the {ending} ending has its own heading",
              f'"{ending}":' in src, ending)


def test_a_fled_finale_does_not_strand_you(content):
    """Walking out of a finale fight left the run unfinishable.

    The last room is a conversation that has already happened, so re-entering
    did nothing at all: no boss, no dialogue, nothing to do, and no way to
    end the run. Fleeing the Signatory now leaves the fight standing, and
    fleeing the Narrator is an ending in its own right.
    """
    from frontends.terminal.main import parse
    from engine.rng import Rng
    from engine.state import MODE_COMBAT, MODE_EXPLORE, MODE_WON

    boss_room = content.floor(13)["boss_room"]

    def at_finale(term=False):
        state, _ = step_mod.new_game(content, 4, "T", "vanguard", "grunk")
        state.floor = 13
        state.room = boss_room
        if term:
            state.add_item("unamended_term")
        step_mod._finale_open(state, content, Rng(1, 0), [])
        return state

    def flee_out(state):
        for _ in range(60):
            if state.mode != MODE_COMBAT:
                return state
            state, _ = step_mod.step(state, actions.flee(), content)
        return state

    # The Signatory: leaving is allowed, but the fight is still there.
    state = at_finale()
    for typed in ("refuse", "yes"):
        state, _ = step_mod.step(state, parse(typed, state, content), content)
    check("refusing starts the fight", state.mode == MODE_COMBAT, state.mode)
    state = flee_out(state)
    check("you can walk out of it", state.mode == MODE_EXPLORE, state.mode)
    check("and it is remembered as unfinished",
          state.flags.get("finale_fight") == "the_signatory",
          state.flags.get("finale_fight"))
    out = []
    step_mod.enter_room(state, content, Rng(2, 0), boss_room, "north", out)
    check("coming back restarts it", state.mode == MODE_COMBAT, state.mode)
    check("against the same boss",
          [e.monster_id for e in state.combat.enemies] == ["the_signatory"],
          [e.monster_id for e in state.combat.enemies])

    # The Narrator: fleeing is the fourth ending, not a stranding.
    state = at_finale(term=True)
    state, _ = step_mod.step(state, parse("withdraw", state, content), content)
    check("withdrawing summons him", state.mode == MODE_COMBAT, state.mode)
    check("under his own name",
          content.t(content.monster("the_narrator")["name_key"]) == "The Narrator",
          content.t(content.monster("the_narrator")["name_key"]))
    state = flee_out(state)
    check("running from him ends the run", state.mode == MODE_WON, state.mode)
    check("with the fourth ending", state.flags.get("ending") == "upstairs",
          state.flags.get("ending"))
    check("and nothing left hanging",
          not state.flags.get("finale_fight"), state.flags.get("finale_fight"))

    # He speaks in his own framing throughout, not as plain prose.
    state = at_finale(term=True)
    _, events = step_mod.step(state, parse("withdraw", state, content), content)
    kinds = [e.kind for e in events]
    check("his turn is narration, not prose",
          kinds.count("Narration") >= 4, kinds)
    check("and the interference runs for the whole fight",
          any(e.kind == "Effect" and e.name == "static" and e.get("persist")
              for e in events), [e.kind for e in events])

    # Which means it has to be turned off however the fight ends.
    state = at_finale(term=True)
    state, _ = step_mod.step(state, parse("withdraw", state, content), content)
    state = flee_out(state)
    check("fleeing clears the interference", state.flags.get("ending") == "upstairs")


def test_the_signatory_accepts_his_own_options(content):
    """The finale offers named choices, so it has to own the command space.

    He sets MODE_CHOICE, and the parser only produced a Choose action for
    yes and no - so ASK and REFUSE parsed as nothing, WITHDRAW became the
    global withdrawal count, and the prompt said [yes/no] while he was
    offering four other things. Nothing you typed reached him.
    """
    from frontends.terminal.main import parse, prompt_for
    from engine.rng import Rng

    def opened(with_term=False):
        state, _ = step_mod.new_game(content, 4, "T", "vanguard", "grunk")
        state.floor = 13
        if with_term:
            state.add_item("unamended_term")
        out = []
        step_mod._finale_open(state, content, Rng(1, 0), out)
        return state, out

    state, out = opened()
    check("he pauses rather than arriving as a wall",
          sum(1 for e in out if e.kind == "Pause") >= 2,
          [e.kind for e in out])
    check("the prompt names his options",
          prompt_for(state).strip() == "[ask/sign/refuse] >",
          prompt_for(state))

    for typed in ("ask", "sign", "refuse", "withdraw", "nonsense", "yes"):
        act = parse(typed, state, content)
        check(f"'{typed}' reaches him",
              act is not None and act.kind == "Choose" and act.arg == typed,
              act and (act.kind, act.arg))

    # Walking the tree: each named option moves to its node.
    state, _ = opened()
    for typed, node in (("ask", "ask"), ("eleven", "eleven"),
                        ("refuse", "confirm_fight"), ("no", "open")):
        state, _ = step_mod.step(state, parse(typed, state, content), content)
        check(f"'{typed}' goes to {node}",
              (state.pending or {}).get("node") == node,
              (state.pending or {}).get("node"))

    # The confirm steps really are yes/no, and say so.
    state, _ = step_mod.step(state, parse("sign", state, content), content)
    check("the confirm prompt is yes/no",
          prompt_for(state).strip() == "[yes/no] >", prompt_for(state))

    # All three endings are reachable.
    state, _ = opened()
    for typed in ("sign", "yes"):
        state, _ = step_mod.step(state, parse(typed, state, content), content)
    check("signing ends the run", state.mode == "won"
          and state.flags.get("ending") == "signed",
          (state.mode, state.flags.get("ending")))

    state, _ = opened()
    for typed in ("refuse", "yes"):
        state, _ = step_mod.step(state, parse(typed, state, content), content)
    check("refusing starts the fight", state.mode == "combat", state.mode)

    # The withdrawal is only offered when you are carrying the term, which
    # the Floor 11 boss drops.
    state, _ = opened()
    check("no withdrawal without the term",
          "withdraw" not in state.pending["options"], state.pending["options"])
    state, _ = opened(with_term=True)
    check("and it is offered with it",
          "withdraw" in state.pending["options"], state.pending["options"])
    state, _ = step_mod.step(state, parse("withdraw", state, content), content)
    check("withdrawing goes somewhere", state.pending is None
          and state.mode != "choice", (state.mode, state.pending))

    # Outside the finale, WITHDRAW is still the global one.
    ordinary, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    check("WITHDRAW still works normally elsewhere",
          parse("withdraw", ordinary, content).kind == "Withdraw")


def test_minigames_own_the_command_space(content):
    """A minigame gets every line typed at it, and gets it first.

    The minigame branch in parse() used to sit AFTER the verb table, so eleven
    of the twenty-six letters were swallowed as commands — "a" attacked, "e",
    "n", "s" and "w" moved, "i" opened the inventory, "y" answered a prompt.
    Hangman could not be played at all: guesses became commands, the engine
    rejected them as "not now", and it read as the game refusing input.
    """
    import string
    from frontends.terminal.main import parse
    from engine.state import MODE_MINIGAME, MODE_EXPLORE
    from engine.rng import Rng
    from engine import minigames

    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    state.mode = MODE_MINIGAME

    stolen = [ch for ch in string.ascii_lowercase
              if (parse(ch, state, content) or actions.look()).kind != "Minigame"]
    check("every single letter reaches the minigame", not stolen, stolen)

    for typed in ("4 3", "liar", "rock", "5", "severability", "no", "yes",
                  "attack", "north", "sheet", "inv"):
        act = parse(typed, state, content)
        check(f"'{typed}' is handed to the minigame",
              act is not None and act.kind == "Minigame" and act.arg == typed,
              act and (act.kind, act.arg))

    # And hangman is actually completable through the real parser.
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    state.minigame = minigames.get("hangman").start(
        state, content, Rng(1, 0), {})
    state.minigame["npc"] = "t"
    state.minigame["game"] = "hangman"
    state.mode = MODE_MINIGAME
    word = state.minigame["word"]
    check("the word is worth guessing", word.isalpha() and len(word) > 3, word)

    for ch in sorted(set(word.lower())):
        if state.mode != MODE_MINIGAME:
            break
        state, _ = step_mod.step(state, parse(ch, state, content), content)
    check("guessing every letter finishes the game",
          state.mode == MODE_EXPLORE, state.mode)
    check("and it is recorded as won", state.stats.minigames_won == 1,
          state.stats.minigames_won)


def test_only_equip_and_unequip_touch_the_slots(content):
    """Nothing else may write to state.equipped.

    Three separate bugs came out of code elsewhere assuming the equipped item
    is also in the bag. It is not — equipping removes it — so any such check
    can only ever fire on a spare, and does the opposite of what it means.
    This pins the rule at the source level rather than one caller at a time.
    """
    import re
    engine_dir = os.path.join(ROOT, "engine")
    offenders = []
    for root, _dirs, files in os.walk(engine_dir):
        if "__pycache__" in root:
            continue
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            for n, line in enumerate(open(path).read().splitlines(), 1):
                if re.search(r"\.equipped\s*(\[[^\]]+\]\s*=|\.pop\(|\.clear\(|\.setdefault\()",
                             line) or re.search(r"del\s+\w+\.equipped\[", line):
                    offenders.append(f"{fname}:{n}")
    allowed = {"step.py", "progression.py"}
    bad = [o for o in offenders if o.split(":")[0] not in allowed]
    check("only step.py and progression.py write to equipped", not bad, bad)

    # And within step.py, only the two functions that are supposed to.
    src = open(os.path.join(engine_dir, "step.py")).read()
    for fn in ("_equip", "_unequip"):
        check(f"{fn} is still where that happens", f"def {fn}(" in src)


def test_selling_a_spare_keeps_what_you_are_wearing(content):
    """Selling a duplicate must not strip the equipped one.

    `sell` used to unequip the slot when the last copy left the bag. Equipping
    removes the item from the bag, so what `sell` matches can never BE the
    equipped item — the check only ever fired on a spare, and stripped the
    armour you were wearing. Selling a spare slag plate took the slag plate
    off your back.
    """
    from engine import shop as shop_mod, combat as combat_mod
    from engine.rng import Rng

    for slot, iid in (("armour", "slag_plate"), ("weapon", "crucible_hammer")):
        state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
        state.equipped[slot] = iid
        state.add_item(iid)                       # the spare that dropped
        ac_before = combat_mod.player_ac(state, content)
        name = content.t(content.item(iid)["name_key"])

        out = []
        shop_mod.sell(state, content, Rng(1, 0), name, out)
        check(f"selling a spare {slot} keeps it equipped",
              state.equipped.get(slot) == iid, state.equipped)
        check(f"the spare {slot} leaves the bag",
              not any(e["id"] == iid for e in state.inventory), state.inventory)
        check("and nothing was paid for with your AC",
              combat_mod.player_ac(state, content) == ac_before)
        check("the sale still paid", state.currency > 0, state.currency)

    # A stacked spare: one goes, one stays, the worn one is untouched.
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    state.equipped["armour"] = "slag_plate"
    state.add_item("slag_plate")
    state.add_item("slag_plate")
    shop_mod.sell(state, content, Rng(1, 0),
                  content.t(content.item("slag_plate")["name_key"]), [])
    left = [e["qty"] for e in state.inventory if e["id"] == "slag_plate"]
    check("selling one of a stacked pair leaves the other", left == [1], left)
    check("and it is still worn", state.equipped.get("armour") == "slag_plate")

    # Unequipping first is how worn gear is actually sold, and that still works.
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    worn = state.equipped["armour"]
    step_mod._unequip(state, content, "armour", [])
    shop_mod.sell(state, content, Rng(1, 0),
                  content.t(content.item(worn)["name_key"]), [])
    check("unequip then sell removes it for good",
          state.equipped.get("armour") is None
          and not any(e["id"] == worn for e in state.inventory),
          (state.equipped, state.inventory))


def test_equip_never_destroys_the_old_gear(content):
    """A full bag plus a stacked duplicate used to eat the swapped-out item.

    remove_item only decremented the stack, so no slot was freed and
    add_item silently refused the old gear while the game said it had gone
    back in the bag.
    """
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    # Distinct ids: add_item stacks by id, so repeating one never fills a slot.
    state.inventory = [{"id": "ppe_vest", "qty": 2}]
    fillers = [i for i in content.items if i != "ppe_vest"]
    for iid in fillers:
        if state.inventory_full():
            break
        state.add_item(iid)
    check("the bag is genuinely full", state.inventory_full(),
          len(state.inventory))
    worn = state.equipped["armour"]

    out = []
    step_mod._equip(state, content, "ppe_vest", out)
    kinds = [e.kind for e in out]
    check("the swap is refused rather than losing the gear",
          "Error" in kinds, kinds)
    check("the old armour is still worn", state.equipped["armour"] == worn)
    total = sum(e["qty"] for e in state.inventory if e["id"] == "ppe_vest")
    check("and both spares are still in the bag", total == 2, state.inventory)


def test_item_labels_are_title_cased(content):
    """The record, the inventory, the shop and the sheet all label items the
    same way. Only the record used to."""
    from engine.content import title_case
    check("apostrophes survive", title_case("janitor's mop") == "Janitor's Mop")
    check("acronyms survive", title_case("a head from RAID-6")
          == "A Head from RAID-6")
    check("small words stay small mid-name",
          title_case("notice of chargeback") == "Notice of Chargeback")
    check("the first word is always capitalised",
          title_case("the keys to number 14") == "The Keys to Number 14")

    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    sheet = step_mod._sheet_payload(state, content)
    check("the sheet title-cases equipped gear",
          sheet["equipped"]["armour"].startswith("Hi-vis Vest"),
          sheet["equipped"])


def test_ability_command(content):
    """ABILITY <partial> is the explicit route and always works, because it
    takes the ability out of the contest with the verb table."""
    from frontends.terminal.main import parse
    from engine.state import MODE_COMBAT

    state, _ = step_mod.new_game(content, 1, "T", "skirmisher", "grunk")
    state.player.abilities = [a["id"] for a in
                              content.classes["skirmisher"]["abilities"]]
    state.mode = MODE_COMBAT

    for typed, kind, arg in (("ability hit", "Ability", "hit_and_run"),
                             ("ability blind", "Ability", "blindside"),
                             ("ab slip", "Ability", "slip"),
                             ("ability", "Abilities", ""),
                             ("ability run", "Abilities", "run")):
        act = parse(typed, state, content)
        check(f"'{typed}' resolves",
              act is not None and act.kind == kind and act.arg == arg,
              act and (act.kind, act.arg))

    # An ambiguous partial lists the candidates rather than picking one.
    _, events = step_mod.step(state, actions.abilities("run"), content)
    text = "\n".join(str(e.data.get("text", "")) for e in events)
    check("an ambiguous partial names both", "Cut and Run" in text
          and "Hit and Run" in text, text)

    # And an unknown one is an error, not a silent no-op.
    _, events = step_mod.step(state, actions.abilities("nonsense"), content)
    check("an unknown ability errors",
          any(e.kind == "Error" for e in events), [e.kind for e in events])


def test_browser_gets_a_completion_vocabulary(content):
    """Pyodide has no tty, so readline never sees stdin and the page has to
    complete for itself. The candidate sets are published, not duplicated."""
    from frontends.terminal.inputline import Input, ABILITY_VERBS

    state, _ = step_mod.new_game(content, 1, "T", "skirmisher", "grunk")
    reader = Input()
    reader.completer.refresh(state, content, ["autosave"])
    snap = reader.completer.snapshot()

    for key in ("verbs", "items", "abilities", "enemies", "saves",
                "pace", "item_verbs", "ability_verbs"):
        check(f"the snapshot carries {key}", key in snap, sorted(snap))
    check("ability is a verb", "ability" in snap["verbs"], snap["verbs"])
    check("the ability verbs are published",
          set(snap["ability_verbs"]) == ABILITY_VERBS, snap["ability_verbs"])
    check("carried items are completable",
          any("ration" in i or "bar" in i for i in snap["items"]), snap["items"])
    check("known abilities are completable",
          "slip" in snap["abilities"], snap["abilities"])

    # The page mirrors this switch; if a verb list moves, both must move.
    check("ability completes against abilities, not items",
          reader.completer.candidates("ability ", "") == snap["abilities"])
    check("equip completes against items",
          reader.completer.candidates("equip ", "") == snap["items"])


def test_settings_report_themselves(content):
    """A bare PACE or EFFECTS says what the setting is.

    There was no way to find out which pacing you were on: a bare PACE fell
    through to the usage error. That matters because at `fast` every
    press-enter break in the game is skipped, so intros arrive as a wall and
    it reads as a missing pause rather than a setting.
    """
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")

    _, events = step_mod.step(state, actions.setting("pace", ""), content)
    said = " ".join(str(e.data.get("text", "")) for e in events)
    check("a bare PACE is not an error",
          not any(e.kind == "Error" for e in events), [e.kind for e in events])
    check("and names the current pacing", "slow" in said.lower(), said[:60])
    check("and explains what the settings do",
          "press-enter" in said.lower(), said)

    state, _ = step_mod.step(state, actions.setting("pace", "manual"), content)
    _, events = step_mod.step(state, actions.setting("pace", ""), content)
    said = " ".join(str(e.data.get("text", "")) for e in events)
    check("it tracks a change", "manual" in said.lower(), said[:60])

    _, events = step_mod.step(state, actions.setting("effects", ""), content)
    said = " ".join(str(e.data.get("text", "")) for e in events)
    check("a bare EFFECTS reports too",
          not any(e.kind == "Error" for e in events) and "on" in said.lower(),
          said[:60])

    # A wrong value is still an error, not a silent no-op.
    _, events = step_mod.step(state, actions.setting("pace", "sideways"),
                              content)
    check("a bad pacing is rejected",
          any(e.kind == "Error" for e in events), [e.kind for e in events])

    # And the default is one that keeps the breaks.
    fresh_state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    check("the default pacing keeps press-enter breaks",
          fresh_state.settings.get("pace", "slow") in ("slow", "manual"),
          fresh_state.settings.get("pace"))


def test_effects_doc_matches_the_game(content):
    """EFFECTS.md lists every floor's effects, so it has to be right.

    A doc nobody checks drifts, and this one is the contract a wrapper
    implements against.
    """
    import os
    import re

    path = os.path.join(ROOT, "EFFECTS.md")
    doc = open(path).read()
    table = doc[doc.index("| Floor | Raises | When |"):]

    claimed = {}
    for line in table.split("\n"):
        m = re.match(r"\| (\d+) \| (.+?) \|", line)
        if m:
            claimed[int(m.group(1))] = m.group(2)

    for n in sorted(content.floors):
        raised = step_mod.floor_effects(content.floor(n))
        row = claimed.get(n, "")
        check(f"EFFECTS.md has a row for floor {n}",
              n in claimed or not raised, n)
        for name in raised:
            check(f"EFFECTS.md lists {name} on floor {n}",
                  f"`{name}`" in row, (n, name, row))
        # And does not claim one that is not there.
        for name in re.findall(r"`(\w+)`", row):
            if name in ("palette", "rainbow", "mono", "full"):
                continue
            # A row may mention an effect the floor does not itself raise,
            # as long as it says so: Floor 11's amend is per room, not a
            # floor effect, and Floor 7's storm is a random event.
            aside = "per room" in row or "s)" in row
            check(f"EFFECTS.md does not invent {name} on floor {n}",
                  name in raised or aside, (n, name, raised))

    # Every effect the engine can raise is documented somewhere in the file.
    from frontends.terminal.effects import Effects
    known = [n for n in dir(Effects)
             if not n.startswith("_") and callable(getattr(Effects, n))]
    for name in known:
        check(f"EFFECTS.md documents `{name}`", f"`{name}`" in doc, name)


def test_floor_twelve_runs_storm_and_ember_together(content):
    """A floor may raise more than one effect.

    Floor 12's own random events are about ash coming down as well as the
    weather, and a single overlay could only say half of that. `effect` now
    takes a list, and the JSON still accepts a plain string everywhere else.
    """
    from engine.rng import Rng
    from engine import saves

    raised = step_mod.floor_effects(content.floor(12))
    check("Floor 12 raises both", raised == ["storm", "ember"], raised)
    for n in (5, 8, 9, 10, 13):
        single = step_mod.floor_effects(content.floor(n))
        check(f"floor {n} still raises exactly one", len(single) == 1, single)
    check("a plain string still works",
          step_mod.floor_effects({"effect": "storm"}) == ["storm"])
    check("and no effect at all is fine", step_mod.floor_effects({}) == [])

    state, _ = step_mod.new_game(content, 7, "T", "vanguard", "grunk")
    out = []
    step_mod.descend(state, content, Rng(state.seed, 0), 12, out)
    fired = [e.name for e in out if e.kind == "Effect"]
    check("both fire on arrival", fired == ["storm", "ember"], fired)
    secs = {e.get("seconds") for e in out if e.kind == "Effect"}
    check("both for the same duration",
          secs == {step_mod.ENTRANCE_EFFECT_SECONDS}, secs)

    blob = saves.encode(state)
    loaded, _ = saves.load_state(blob, content)
    replayed = [e.name for e in step_mod.resume(loaded, content)
                if e.kind == "Effect"]
    check("both replay on a load", replayed == ["storm", "ember"], replayed)

    # And both are torn down on the way out, as separate events rather than
    # one event carrying a list.
    out = []
    step_mod.descend(state, content, Rng(state.seed, 1), 13, out)
    ended = [e.name for e in out if e.kind == "EffectEnd"]
    check("both end on the way down", ended == ["storm", "ember"], ended)
    check("each end names one effect",
          all(isinstance(n, str) for n in ended), ended)


def test_boss_threshold_effect(content):
    """The floor effect kicks back in on the room outside the boss door,
    once, so the last stretch is set without the overlay nagging."""
    from engine.rng import Rng
    for floor_n in (9, 12, 13):
        floor = content.floor(floor_n)
        thresholds = step_mod.boss_threshold_rooms(floor)
        check(f"floor {floor_n} has a room outside the boss door",
              thresholds, thresholds)

        state = fresh(content, floor=floor_n)
        rng = Rng(state.seed, 0)
        room_id = sorted(thresholds)[0]
        out = []
        step_mod.enter_room(state, content, rng, room_id, "s", out)
        fired = [e.name for e in out if e.kind == "Effect"]
        for name in step_mod.floor_effects(floor):
            check(f"floor {floor_n} re-fires {name} at the boss threshold",
                  name in fired, fired)
        secs = [e.get("seconds") for e in out
                if e.kind == "Effect"
                and e.name == step_mod.floor_effects(floor)[0]]
        want = step_mod.floor_effect_seconds(floor)
        check(f"floor {floor_n} threshold burst runs for {want:.0f}s",
              secs == [want], secs)

        out = []
        step_mod.enter_room(state, content, rng, room_id, "s", out)
        again = [e.name for e in out
                 if e.kind == "Effect"
                 and e.name in step_mod.floor_effects(floor)]
        check(f"floor {floor_n} does not re-fire it a second time",
              not again, again)


def test_every_state_install_announces(content):
    """Any path that installs a new state must call resume(), or the browser
    is never told the palette or the floor effect. The post-run restart path
    was missing it."""
    src = open(os.path.join(ROOT, "frontends", "terminal", "main.py")).read()
    lines = src.splitlines()
    missing = []
    for i, line in enumerate(lines):
        if "renderer.set_palette(" in line:
            if "step_mod.resume(" not in "\n".join(lines[i:i + 6]):
                missing.append(i + 1)
    check("every set_palette is followed by resume()", not missing, missing)


def test_persistent_storm(content):
    """Clause 12's storm is a timed burst at the start of the floor, not a
    floor-long hold, and a floor left mid-burst still tidies up on the way
    to 13."""
    from engine.rng import Rng
    state, _ = step_mod.new_game(content, 1, "T", "vanguard", "grunk")
    out = []
    step_mod.descend(state, content, Rng(state.seed, 0), 12, out)
    effects = [e for e in out if e.kind == "Effect"]
    check("floor 12 raises the storm on arrival",
          effects and effects[0].name == "storm", effects)
    check("as a timed burst, not persistent",
          effects and effects[0].get("persist") is not True
          and effects[0].get("seconds") == step_mod.ENTRANCE_EFFECT_SECONDS,
          effects)

    out = []
    step_mod.descend(state, content, Rng(state.seed, 1), 13, out)
    ends = [e for e in out if e.kind == "EffectEnd"]
    check("descending to 13 tidies up the storm regardless",
          ends and ends[0].name == "storm", [e.kind for e in out][:4])
    raised = [e.name for e in out if e.kind == "Effect"]
    check("and floor 13 raises its own on arrival", raised == ["blank"], raised)

    check("floor 13 has its own effect",
          content.floor(13).get("effect") == "blank")
    check("floor 12 no longer carries the old persist flag",
          content.floor(12).get("effect_persist") is None)


def test_every_stat_is_used(content):
    """STR, INT and CHA had no use outside one weapon stat each."""
    engine_dir = os.path.join(ROOT, "engine")
    src = ""
    for root, _dirs, files in os.walk(engine_dir):
        for fname in files:
            if fname.endswith(".py"):
                src += open(os.path.join(root, fname)).read()
    for stat in ("str", "dex", "con", "int", "cha"):
        check(f"{stat.upper()} is read somewhere in the engine",
              f'mod("{stat}")' in src, "never read")


def test_companion_passives_are_real(content):
    """All four passives were flavour text with no implementation."""
    from engine import combat as cmb
    from engine.rng import Rng

    flags = {"pip": "inspects_chests", "grunk": "absorbs_pounce",
             "cube": "minigame_edge", "bartleby": "fire_resist"}
    engine_src = ""
    for root, _dirs, files in os.walk(os.path.join(ROOT, "engine")):
        for fname in files:
            if fname.endswith(".py"):
                engine_src += open(os.path.join(root, fname)).read()
    for cid, flag in flags.items():
        check(f"{cid}'s passive is declared",
              content.companions[cid].get(flag) is not None)
        check(f"{cid}'s passive is implemented", flag in engine_src)

    # Bartleby's fire resistance needs something that deals fire.
    fire = [m for m in content.monsters.values()
            if any(a.get("type") == "fire" for a in m["attacks"])]
    check("something in the game actually deals fire damage", bool(fire),
          "fire resistance would be meaningless")

    # And the breath must fire.
    state, _ = step_mod.new_game(content, 4, "T", "vanguard", "bartleby")
    state.flags["tut.combat"] = True
    state.player.hp_max = 900
    state.player.hp = 900
    state.player.stats["str"] = 6
    rng = Rng(2, 0)
    cmb.start_combat(state, content, rng, ["queue", "queue"],
                     source_room=state.room)
    state.rng_counter = rng.counter
    breathed = False
    for _ in range(20):
        if state.mode != "combat":
            break
        state, events = step_mod.step(state, actions.attack(), content)
        breathed = breathed or any(
            "breathes" in str(e.data.get("text", "")) for e in events)
    check("Bartleby's breath weapon goes off", breathed)


def test_persistence_is_a_weapon(content):
    """It described itself as a weapon and went to the record, unusable."""
    from engine import loot as loot_mod
    from engine.rng import Rng
    state = fresh(content)
    loot_mod.give(state, content, Rng(1, 0), ["persistence"], [])
    check("it goes in the bag", state.has_item("persistence"))
    check("not in the record", "persistence" not in state.keepsakes)
    state, _ = step_mod.step(state, actions.equip("persistence"), content)
    check("and it can be wielded",
          state.equipped.get("weapon") == "persistence")


def test_every_level_has_its_own_line(content):
    for level in range(2, 21):
        check(f"level {level} has standing text",
              content.raw(f"standing.{level}") is not None)


def test_likeness_is_beatable(content):
    """It was three times your health at two better than your armour."""
    from engine import combat as cmb
    from engine.rng import Rng
    state = fresh(content, floor=6)
    state.player.hp_max = 200
    state.player.hp = 200
    rng = Rng(1, 0)
    cmb.start_combat(state, content, rng, ["the_likeness"],
                     source_room=state.room)
    boss = state.combat.enemies[0]
    check("it no longer exceeds your armour",
          boss.ac <= cmb.player_ac(state, content), boss.ac)
    check("and its health is a multiple you can chew through",
          boss.hp_max <= state.player.hp_max * 2, boss.hp_max)


def test_armour_variety(content):
    """The same plate used to be the only armour on a floor."""
    from engine import loot
    from engine.rng import Rng
    for floor_n in (3, 6, 9, 12):
        seen = set()
        for seed in range(60):
            for iid in loot.roll_table(content, Rng(seed, 0), f"f{floor_n}_good"):
                if content.item(iid).get("slot") == "armour":
                    seen.add(iid)
        check(f"floor {floor_n} drops more than one armour", len(seen) > 1, seen)


def test_money_sinks_work(content):
    """Expensive shop items have to actually do something."""
    state = fresh(content)
    for iid in ("silent_partner", "the_franchise", "second_opinion"):
        check(f"{iid} is expensive", content.item(iid)["price"] >= 2000)
        state.add_item(iid)
    before = dict(state.player.stats)
    state, _ = step_mod.step(state, actions.use("silent_partner"), content)
    check("the partner raises every stat",
          all(state.player.stats[k] == before[k] + 1 for k in before))
    state, _ = step_mod.step(state, actions.use("the_franchise"), content)
    check("the franchise improves sale prices",
          state.flags.get("sell_multiplier", 1) > 1)
    state.continue_available = False
    state, _ = step_mod.step(state, actions.use("second_opinion"), content)
    check("the second opinion grants a reprieve", state.continue_available)


def test_merch_payoff(content):
    """The merchandise was three-clip vendor trash with no reason to keep it."""
    from engine.rng import Rng
    state = fresh(content)
    for _ in range(5):
        state.add_item("own_merch")
    out = []
    step_mod._check_merch(state, content, Rng(1, 0), out)
    check("five pieces pays off", state.has_item("royalty_statement"),
          "no reward for collecting merch")


def test_safe_rooms_mention_the_photo(content):
    state = fresh(content)
    safe = next(rid for rid, r in content.floor(1)["rooms"].items()
                if r.get("kind") == "safe")
    out = []
    step_mod.describe_room(state, content, Rng(1, 0), safe, True, out)
    text = " ".join(str(e.data.get("text", "")) for e in out)
    check("a safe room points at the photograph", "PHOTO" in text.upper(), text[:60])


def test_combat_buffs_are_free_and_combat_only(content):
    """A buff that costs the round is a buff nobody drinks."""
    from engine import combat as cmb
    from engine.rng import Rng

    state = fresh(content)
    state.add_item("root_token")
    state, events = step_mod.step(state, actions.use("root_token"), content)
    check("a combat buff refuses outside a fight",
          any(e.kind == "Error" for e in events))
    check("and is not consumed", state.has_item("root_token"))

    state.flags["tut.combat"] = True
    rng = Rng(2, 0)
    cmb.start_combat(state, content, rng, ["intern"], source_room=state.room)
    state.rng_counter = rng.counter
    state, events = step_mod.step(state, actions.use("root_token"), content)
    check("the buff applies", "advantage" in state.player.statuses)
    check("and costs no turn",
          not any(e.kind == "AttackResolved" for e in events))


def test_photo_has_a_line_per_floor(content):
    for floor_n in sorted(content.floors):
        check(f"floor {floor_n} has its own photograph line",
              content.raw(f"secret.photo_floor_{floor_n}") is not None)
    check("a repeat safe room says it is the same one",
          content.raw("rooms_common.photo_same_floor") is not None)


def test_mimic_exists_and_scales(content):
    from engine import combat as cmb
    from engine.rng import Rng
    check("the mimic is a real monster", "the_contents" in content.monsters)
    check("and scales with depth",
          content.monster("the_contents").get("scales_with_floor"))

    shallow, deep = [], []
    for floor_n, bucket in ((2, shallow), (12, deep)):
        state = fresh(content, floor=floor_n)
        rng = Rng(1, 0)
        cmb.start_combat(state, content, rng, ["the_contents"],
                         source_room=state.room)
        bucket.append(state.combat.enemies[0].hp_max)
    check("a deep mimic is tougher than a shallow one",
          deep[0] > shallow[0] * 1.5, (shallow, deep))


def test_floor_effects_are_placed(content):
    check("floor 5 is cold", content.floor(5).get("effect") == "cold")
    check("floor 7 has no fixed effect", not content.floor(7).get("effect"))
    check("floor 7 rains as an event",
          any(e.get("effect") == "storm"
              for e in content.floor(7).get("random_events", [])))
    check("floor 8 runs the colour gag",
          content.floor(8).get("effect") == "colour_gag")
    from frontends.terminal.effects import Effects
    for name in ("cold", "colour_gag", "storm", "party"):
        check(f"{name} is implemented", hasattr(Effects, name))


def test_record_is_title_cased(content):
    from frontends.terminal.render import _title_case
    check("apostrophes survive",
          _title_case("the Greeter's name badge") == "The Greeter's Name Badge",
          _title_case("the Greeter's name badge"))
    check("small words stay small",
          _title_case("a scale from the Reaper") == "A Scale from the Reaper",
          _title_case("a scale from the Reaper"))


def test_late_game_effects_are_hooked(content):
    """Every declared effect must have an implementation and a trigger."""
    from frontends.terminal.effects import Effects
    expected = ("storm", "party", "cold", "colour_gag", "sever", "session",
                "amend", "ember", "blank", "static", "signature", "lowhp")
    for name in expected:
        check(f"{name} is implemented", hasattr(Effects, name))

    engine_src = ""
    for root, _dirs, files in os.walk(os.path.join(ROOT, "engine")):
        for fname in files:
            if fname.endswith(".py"):
                engine_src += open(os.path.join(root, fname)).read()
    floors = open(os.path.join(ROOT, "content", "floors", "09.json")).read()
    for name in ("amend", "ember", "static", "signature", "lowhp"):
        check(f"{name} is triggered somewhere", f'"{name}"' in engine_src, name)
    for floor_n, name in ((5, "cold"), (8, "colour_gag"), (9, "sever"),
                          (10, "session"), (12, "storm"), (13, "blank")):
        check(f"floor {floor_n} triggers {name}",
              content.floor(floor_n).get("effect") == name)

    # These no longer hold for the floor - they fire as timed bursts on
    # arrival and again at the boss threshold (see _effect_should_fire in
    # step.py) - so the old effect_persist marker should be gone, not just
    # unused.
    for floor_n in (5, 8, 9, 10, 12, 13):
        check(f"floor {floor_n} no longer declares effect_persist",
              content.floor(floor_n).get("effect_persist") is None)


def test_low_hp_warning(content):
    from engine.state import MODE_COMBAT, MODE_EXPLORE
    state = fresh(content, floor=9)
    state.player.hp_max = 200
    state.player.hp = 30
    state.mode = MODE_COMBAT
    out = []
    step_mod._low_hp_watch(state, out)
    check("a warning is raised under a quarter health in combat",
          any(e.kind == "Effect" and e.name == "lowhp" for e in out))
    state.player.hp = 180
    out = []
    step_mod._low_hp_watch(state, out)
    check("and cleared on recovery",
          any(e.kind == "EffectEnd" and e.name == "lowhp" for e in out))

    shallow = fresh(content, floor=2)
    shallow.player.hp_max = 200
    shallow.player.hp = 10
    shallow.mode = MODE_COMBAT
    out = []
    step_mod._low_hp_watch(shallow, out)
    check("early floors stay quiet", not out)

    exploring = fresh(content, floor=9)
    exploring.player.hp_max = 200
    exploring.player.hp = 30
    exploring.mode = MODE_EXPLORE
    out = []
    step_mod._low_hp_watch(exploring, out)
    check("low health outside combat stays quiet", not out)

    # Ending the fight while still low should drop the vignette rather than
    # let it linger over the map.
    exploring.mode = MODE_COMBAT
    out = []
    step_mod._low_hp_watch(exploring, out)
    check("combat entry raises the warning",
          any(e.kind == "Effect" and e.name == "lowhp" for e in out))
    exploring.mode = MODE_EXPLORE
    out = []
    step_mod._low_hp_watch(exploring, out)
    check("leaving combat clears the warning even while still low",
          any(e.kind == "EffectEnd" and e.name == "lowhp" for e in out))


def test_inventory_upgrades(content):
    """Bag upgrades must raise the cap and survive a save."""
    state = fresh(content)
    base = state.cap()
    state.inventory_bonus += 3
    check("bag upgrade raises the cap", state.cap() == base + 3)
    blob = saves.encode(state)
    reloaded, _ = saves.load_state(blob, content, allow_dead=True)
    check("bag upgrade survives a save", reloaded.cap() == base + 3)


def test_filing_the_withdrawal_lightens_the_narrator(content):
    """The thirteen refusals have to buy something.

    The unamended term drops off The Amendment automatically and cannot be
    missed, so it makes the withdrawal the default rather than something
    earned. The thirteen WITHDRAWs are what the memos build up on every
    floor, and they bought nothing but a keepsake.

    They are a discount, not a gate: the ending stays reachable without
    them, because a player locked out would have nothing on screen telling
    them why. Unfiled, the Narrator is UNFILED_NARRATOR_HP heavier.
    """
    from engine.rng import Rng

    def narrator_hp(filed, reentry=False, seed=3):
        state, _ = step_mod.new_game(content, 9, "T", "vanguard", "pip")
        state.floor, state.room = 13, "13-r40"
        state.add_keepsake("unamended_term")
        if filed:
            state.add_keepsake("notice_of_withdrawal")
        out = []
        if reentry:
            state.flags["finale_done"] = True
            state.flags["finale_fight"] = "the_narrator"
            step_mod.enter_room(state, content, Rng(seed, 0), "13-r40", "s", out)
        else:
            step_mod._narrator_turns(state, content, Rng(seed, 0), out)
        return state.combat.enemies[0].hp_max

    penalty = step_mod.UNFILED_NARRATOR_HP
    for seed in (3, 11, 77):
        filed, unfiled = narrator_hp(True, seed=seed), narrator_hp(False, seed=seed)
        check("filing the withdrawal is worth exactly the penalty",
              unfiled - filed == penalty, f"seed {seed}: {unfiled} - {filed}")

    # Walking out and coming back must not shed it, or fleeing is a discount
    # of its own.
    for seed in (3, 11, 77):
        filed = narrator_hp(True, reentry=True, seed=seed)
        unfiled = narrator_hp(False, reentry=True, seed=seed)
        check("a restarted fight weighs him the same",
              unfiled - filed == penalty, f"seed {seed}: {unfiled} - {filed}")

    # And the egg must stay reachable rather than becoming a gate.
    state, _ = step_mod.new_game(content, 9, "T", "vanguard", "pip")
    state.floor, state.room = 13, "13-r40"
    state.add_keepsake("unamended_term")
    options = step_mod._finale_options(state, content, "open")
    check("the withdrawal is offered without the notice",
          "withdraw" in [o[0] for o in options], [o[0] for o in options])


def test_the_two_trinkets_do_something(content):
    """The settlement chit and the business card used to be vendor trash.

    Two of the four classes opened with a second item that had no `use` block
    and no code behind it, while the other two opened with a working
    consumable. Both now earn their slot.
    """
    from engine import minigames, progression
    from engine.rng import Rng
    from engine.state import MODE_CHOICE, MODE_EXPLORE, MODE_MINIGAME

    def avg(dmg):
        n, rest = dmg.split("d")
        faces = int(rest.split("+")[0])
        flat = int(rest.split("+")[1]) if "+" in rest else 0
        return int(n) * (faces + 1) / 2 + flat


    def to_a_loss(chit=True):
        state, _ = step_mod.new_game(content, 3, "T", "vanguard", "pip")
        if not chit:
            state.remove_item("settlement_chit")
        state.minigame = minigames.get("rps").start(state, content, Rng(1, 0), {})
        state.minigame.update(npc="vending_ghost", game="rps", config={})
        state.mode = MODE_MINIGAME
        for _ in range(40):
            if state.mode != MODE_MINIGAME:
                break
            state, _ = step_mod.step(state, actions.Action("Minigame", "rock"),
                                     content)
        return state

    # No chit: the loss resolves where it always did.
    state = to_a_loss(chit=False)
    check("without the chit a loss just resolves", state.mode == MODE_EXPLORE,
          state.mode)

    # With one: offered, not spent for you.
    state = to_a_loss()
    check("the chit is offered on a loss",
          state.mode == MODE_CHOICE
          and (state.pending or {}).get("kind") == "chit_retry", state.mode)

    hurt = to_a_loss()
    before = hurt.player.hp
    hurt, _ = step_mod.step(hurt, actions.Action("Choose", "yes"), content)
    check("taking it replays the game", hurt.mode == MODE_MINIGAME
          and hurt.minigame is not None)
    check("and spends the chit", not hurt.has_item("settlement_chit"))
    check("and costs no health", hurt.player.hp == before)

    kept = to_a_loss()
    before = kept.player.hp
    kept, _ = step_mod.step(kept, actions.Action("Choose", "no"), content)
    check("declining keeps the chit", kept.has_item("settlement_chit"))
    check("and takes the penalty", kept.player.hp < before)

    # One retry only — a second loss is a second loss.
    again = to_a_loss()
    again, _ = step_mod.step(again, actions.Action("Choose", "yes"), content)
    for _ in range(40):
        if again.mode != MODE_MINIGAME:
            break
        again, _ = step_mod.step(again, actions.Action("Minigame", "rock"), content)
    check("the retry is not offered twice", again.mode == MODE_EXPLORE
          and again.pending is None, again.mode)

    # The card presses at Level 6, and only if it was kept.
    def level_to(target, keep=True):
        state, _ = step_mod.new_game(content, 4, "T", "advocate", "pip")
        if not keep:
            state.remove_item("business_card")
        out = []
        for lvl in range(2, target + 1):
            state.player.xp = progression.XP_TABLE[lvl - 1]
            progression.award_xp(state, content, Rng(1, 0), 0, out)
        return state

    def has(state, iid):
        return any(e["id"] == iid for e in state.inventory)

    check("the card is still a card at level 5",
          not has(level_to(5), "pressed_card"))
    pressed = level_to(progression.CARD_PRESSES_AT)
    check("it presses at level 6", has(pressed, "pressed_card")
          and not has(pressed, "business_card"))
    check("selling it early forfeits it",
          not has(level_to(7, keep=False), "pressed_card"))

    # The Floor 6 die-cutter: the laminate's other half of the same idea.
    def diecut(hold):
        state, _ = step_mod.new_game(content, 6, "T", "vanguard", "pip")
        state.floor = 6
        if hold:
            state.add_item("laminate")
        out = []
        step_mod.enter_room(state, content, Rng(2, 0), "06-r09", "south", out)
        return state, " ".join(str(getattr(e, "text", "")) for e in out)

    cut, _ = diecut(True)
    check("the die-cutter cuts a held laminate",
          has(cut, "cut_laminate") and not has(cut, "laminate"))
    idle, text = diecut(False)
    check("and comments if you have none", not has(idle, "cut_laminate")
          and "sword" in text.lower())

    # Entering twice must not mint a second one.
    out = []
    step_mod.enter_room(cut, content, Rng(3, 0), "06-r09", "south", out)
    check("re-entering cuts nothing further",
          sum(1 for e in cut.inventory if e["id"] == "cut_laminate") == 1)

    # Floor 7 tier: ahead of Floor 6's weapon, behind Floor 8's.
    blade = content.item("cut_laminate")
    check("the cut laminate is a Floor 7 weapon",
          avg(blade["dmg"]) > avg(content.item("signature_pen")["dmg"])
          and avg(blade["dmg"]) < avg(content.item("crucible_hammer")["dmg"]),
          avg(blade["dmg"]))
    check("the uncut laminate is not a weapon",
          "slot" not in content.item("laminate"))

    # Ahead of the tier it arrives in, behind the class's next real weapon.
    card = content.item("pressed_card")
    check("the pressed card scales on charisma", card["stat"] == "cha",
          card["stat"])
    check("it beats the tier it arrives in",
          avg(card["dmg"]) > avg(content.item("actuary_pen")["dmg"]))
    check("but not the class's next weapon",
          avg(card["dmg"]) < avg(content.item("signature_pen")["dmg"]))


def test_the_haggle_line_tells_the_truth(content):
    """Charisma moves every price, and nothing said so.

    Haggling is passive and always on, so there is no verb to find — but the
    vending machine's hint used to say it "does not haggle", which read as a
    promise that somewhere you could. The stall now says what CHA is doing,
    and the number it prints has to match the number it charges.
    """
    from engine import shop as shop_mod

    for cha in (5, 8, 10, 12, 16, 22):
        state = fresh(content)
        state.player.stats["cha"] = cha
        note = shop_mod.haggle_note(state, content)
        charged = shop_mod.haggle(state, 100)

        if cha in (10,):
            check("no line when charisma changes nothing", note is None, note)
            check("and the price is the ticket price", charged == 100, charged)
            continue

        check(f"CHA {cha} gets a line", note is not None)
        stated = int("".join(ch for ch in note.split("%")[0][-3:] if ch.isdigit()))
        actual = abs(100 - charged)
        check(f"CHA {cha}: the line matches the price",
              stated == actual, f"says {stated}%, charges {actual}%")
        direction = "under" if charged < 100 else "over"
        check(f"CHA {cha}: and matches the direction", direction in note, note)

    # The machine must not advertise a verb that does not exist.
    machine_hint = content.t("shop.machine_hint")
    check("the machine hint promises no haggling",
          "haggle" not in machine_hint.lower(), machine_hint)


def test_a_beat_before_the_board(content):
    """The stall and every minigame get a press before the prompt.

    An NPC's intro carries the rules, the challenge and the stakes, and
    without a gate it scrolls off under the stock list or the board the
    instant it prints. `pace fast` skips these like every other press.
    """
    def an_npc_room(floor, key):
        for rid, room in content.floor(floor)["rooms"].items():
            for entry in room.get("contents", []):
                if entry.get("type") == "npc" and key in entry:
                    return rid
        return None

    def kinds_on_talk(floor, rid):
        state, _ = step_mod.new_game(content, 5, "T", "vanguard", "pip")
        state.floor, state.room = floor, rid
        state, out = step_mod.step(state, actions.talk(), content)
        return [e.kind for e in out]

    seen_game = seen_shop = 0
    for floor in range(1, 14):
        for key, target in (("minigame", "MinigamePrompt"), ("shop", "Shop")):
            rid = an_npc_room(floor, key)
            if not rid:
                continue
            kinds = kinds_on_talk(floor, rid)
            if target not in kinds:
                continue
            idx = kinds.index(target)
            check(f"floor {floor}: a beat before the {key}",
                  idx > 0 and kinds[idx - 1] == "Pause", kinds[-4:])
            # Intros that already end on <pause> must not gate twice.
            check(f"floor {floor}: and only one",
                  not any(kinds[i] == "Pause" and kinds[i + 1] == "Pause"
                          for i in range(len(kinds) - 1)), kinds)
            if key == "minigame":
                seen_game += 1
            else:
                seen_shop += 1

    check("minigame hosts were actually exercised", seen_game >= 5, seen_game)
    check("and at least one stall", seen_shop >= 1, seen_shop)


def test_build_strings_agree(content):
    """The VERSION file, main.BUILD and effects.BUILD must be one string.

    They drifted once — effects.BUILD sat five days behind — and the `fx`
    diagnostic is the thing people are asked to paste in a bug report, so
    it reported a build that was not running.
    """
    import re as _re
    from frontends.terminal import main as main_mod
    from frontends.terminal import effects as fx_mod

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version_file = os.path.join(os.path.dirname(root), "VERSION")
    check("main and effects agree", main_mod.BUILD == fx_mod.BUILD,
          f"{main_mod.BUILD} vs {fx_mod.BUILD}")

    if not os.path.exists(version_file):
        return          # python/ is usable on its own, without the wrapper
    stamped = open(version_file, encoding="utf-8").readline()
    match = _re.search(r"build:\s*(\S+)", stamped)
    check("VERSION file agrees", bool(match) and match.group(1) == main_mod.BUILD,
          f"VERSION says {match.group(1) if match else '?'}, code says {main_mod.BUILD}")

    index = os.path.join(os.path.dirname(root), "index.html")
    if os.path.exists(index):
        want = "?v=" + main_mod.BUILD.replace("-", "")
        html = open(index, encoding="utf-8").read()
        check("index.html cache-busters agree", want in html, f"expected {want}")


def test_effects_toggle(content):
    """`effects off` mutes the visual layer instantly and `effects on`
    resumes it; the setting survives a save round-trip either way."""
    state = fresh(content, floor=7)   # rainbow floor: something is showing
    check("effects are on by default", step_mod._effects_enabled(state))

    state, out = step_mod.step(state, actions.setting("effects", "off"), content)
    check("turning off is acknowledged",
          any(e.kind == "Plain" for e in out))
    check("and tears down the current palette right away",
          any(e.kind == "PaletteChanged" and e.data["palette"] == "full"
              for e in out))
    check("effects are now off", not step_mod._effects_enabled(state))

    state, out = step_mod.step(state, actions.look(), content)
    check("no effect-family events leak through while off",
          not any(e.kind in ("Effect", "EffectEnd", "PaletteChanged")
                  for e in out))

    blob = saves.encode(state)
    reloaded, _ = saves.load_state(blob, content, allow_dead=True)
    check("the off setting survives a save",
          not step_mod._effects_enabled(reloaded))
    resumed = step_mod.resume(reloaded, content)
    check("and a resumed session stays quiet too",
          not any(e.kind in ("Effect", "EffectEnd", "PaletteChanged")
                  for e in resumed))

    reloaded, out = step_mod.step(reloaded, actions.setting("effects", "on"),
                                  content)
    check("turning back on re-syncs the current floor's palette",
          any(e.kind == "PaletteChanged" and e.data["palette"] == "rainbow"
              for e in out))
    check("effects are on again", step_mod._effects_enabled(reloaded))

    bad = fresh(content, floor=7)
    _, out = step_mod.step(bad, actions.setting("effects", "sideways"),
                           content)
    check("a bad argument gets a usage error, not a crash",
          any(e.kind == "Error" for e in out))


def main():
    content = load_from_disk(os.path.join(ROOT, "content"))
    for test in (test_toll_charged_once, test_toll_not_recharged,
                 test_no_wasted_items, test_full_pack_keeps_loot,
                 test_save_roundtrip, test_ability_progression,
                 test_companion_lifecycle, test_portrait_shows_description,
                 test_portrait_has_no_verb, test_save_ordering,
                 test_dice_input, test_equip_swaps_with_bag,
                 test_key_items_cannot_be_dropped, test_wall_secret,
                 test_class_style_text, test_round_status,
                 test_key_items_are_not_carried,
                 test_enemy_drops_are_recoverable, test_keepsake_migration,
                 test_minigame_hosts_are_named, test_art_lines_fit,
                 test_floor_narration_consistency, test_big_beats_pause,
                 test_floor_clear_banner_is_per_floor, test_finale_paths,
                 test_easter_eggs, test_generated_saves,
                 test_rainbow_is_floor_seven_only,
                 test_rainbow_starts_at_the_reveal, test_save_bundle,
                 test_no_unresolved_keys, test_new_minigames,
                 test_effects_degrade, test_effects_degrade_in_browser,
                 test_browser_hook_fires, test_resume_reannounces_presentation,
                 test_boss_threshold_effect,
                 test_floor_twelve_runs_storm_and_ember_together,
                 test_settings_report_themselves,
                 test_effects_doc_matches_the_game,
                 test_ability_names_beat_the_verb_table,
                 test_error_text_wraps_to_the_real_width,
                 test_equipping_empties_the_slot,
                 test_equip_never_destroys_the_old_gear,
                 test_selling_a_spare_keeps_what_you_are_wearing,
                 test_only_equip_and_unequip_touch_the_slots,
                 test_every_choice_prompt_accepts_its_own_options,
                 test_a_fled_finale_does_not_strand_you,
                 test_the_signatory_accepts_his_own_options,
                 test_minigames_own_the_command_space,
                 test_trapdoor_drops_you_somewhere_else,
                 test_read_is_the_verb_for_walls,
                 test_notes_are_readable_and_kept,
                 test_memo_text_reflows,
                 test_healing_drops_belong_to_their_floor,
                 test_carried_down_items_do_not_lie_about_where_they_are,
                 test_merchant_still_announced_on_a_revisit,
                 test_every_stall_has_its_own_stock,
                 test_seniors_are_a_fight_not_a_potion_treadmill,
                 test_the_long_floors_have_somewhere_to_stop,
                 test_map_heading_names_the_floor_first,
                 test_map_numbers_only_the_last_nine_steps,
                 test_the_two_stalls_are_not_on_top_of_each_other,
                 test_late_stalls_sell_more_than_potions,
                 test_the_intro_still_stops_to_be_read,
                 test_one_press_per_beat,
                 test_indented_lines_wrap_aligned,
                 test_stalls_sell_what_runs_out,
                 test_a_machine_never_speaks_as_the_merchant,
                 test_tictactoe_can_actually_be_won,
                 test_memo_tips_are_true,
                 test_the_companion_is_accounted_for,
                 test_the_disputants_add_up,
                 test_one_narrator_no_settings,
                 test_the_voices_have_enough_to_say,
                 test_every_secret_is_hinted_somewhere,
                 test_stashes_are_silent_until_you_look,
                 test_blackjack_is_playable,
                 test_later_floors_are_worth_walking,
                 test_item_labels_are_title_cased,
                 test_ability_command,
                 test_browser_gets_a_completion_vocabulary,
                 test_persistent_storm, test_every_state_install_announces,
                 test_floor_effects_and_pacing, test_carl_is_announced,
                 test_wall_secret_is_broken_up,
                 test_narrator_final_boss,
                 test_signed_ending_still_ends, test_inventory_upgrades,
                 test_filing_the_withdrawal_lightens_the_narrator,
                 test_the_two_trinkets_do_something,
                 test_the_haggle_line_tells_the_truth,
                 test_a_beat_before_the_board,
                 test_build_strings_agree,
                 test_effects_toggle):
        print(f"\n{test.__name__}")
        test(content)
    print(f"\n{len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
