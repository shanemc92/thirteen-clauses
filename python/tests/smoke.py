"""Headless bot playthroughs. Catches crashes the terminal loop would hide.

    python3 tests/smoke.py [runs]
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import actions, saves, step as step_mod            # noqa: E402
from engine.content import load_from_disk                      # noqa: E402
from engine.rng import Rng                                     # noqa: E402
from engine.state import (MODE_CHOICE, MODE_COMBAT, MODE_DEAD,  # noqa: E402
                          MODE_MINIGAME, MODE_WON)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ["n", "s", "e", "w"]
CLASSES = ["vanguard", "skirmisher", "hexwright", "advocate"]
COMPANIONS = ["pip", "grunk", "cube", "bartleby"]


def _best_upgrade(state, content):
    """Equip the strongest weapon and armour carried. A player would."""
    def score(iid, slot):
        item = content.item(iid)
        if item.get("slot") != slot:
            return None
        if slot == "armour":
            return item.get("ac", 0)
        from engine.dice import parse
        return parse(item.get("dmg", "1d4")).average()

    for slot in ("weapon", "armour"):
        current = state.equipped.get(slot)
        best, best_score = current, (score(current, slot) if current else -1)
        for entry in state.inventory:
            value = score(entry["id"], slot)
            if value is not None and value > (best_score or -1):
                best, best_score = entry["id"], value
        if best and best != current:
            return best
    return None


def _potion_count(state, content):
    return sum(e["qty"] for e in state.inventory
               if content.item(e["id"]).get("use", {}).get("op") == "heal")


def _stock_slot(state, content, healing):
    """Index of an affordable stock item, preferring (or avoiding) healing."""
    for i, row in enumerate(state.shop["stock"], 1):
        if row["sold"] or row["price"] > state.currency:
            continue
        heals = content.item(row["item"]).get("use", {}).get("op") == "heal"
        if heals == healing:
            return str(i)
    return None


def _best_potion(state, content):
    best, best_avg = None, 0
    from engine.dice import parse
    for entry in state.inventory:
        use = content.item(entry["id"]).get("use")
        if not use or use.get("op") != "heal":
            continue
        avg = parse(use["dice"]).average()
        if avg > best_avg:
            best, best_avg = entry["id"], avg
    return best


def _least_useful(state, content):
    """Drop junk first, then spare gear, and hold on to healing.

    Dropping potions to keep a second suit of armour is how the bot used to
    arrive on Floor 8 fully armoured with nothing to drink.
    """
    def rank(entry):
        item = content.item(entry["id"])
        if item.get("use"):
            return 3                     # consumables last
        if item.get("slot"):
            return 2                     # spare weapons and armour
        return 1                         # merch, laminate, keepsakes

    best, best_key = None, None
    for entry in state.inventory:
        item = content.item(entry["id"])
        if item.get("key_item"):
            continue
        if state.equipped.get(item.get("slot")) == entry["id"]:
            continue
        key = (rank(entry), item.get("price", 10))
        if best_key is None or key < best_key:
            best, best_key = entry["id"], key
    return best


def bot_action(state, content, rng):
    if state.mode == MODE_CHOICE:
        return actions.Action("Choose", "yes")
    if state.mode == "shop":
        # Healing first, always. The bot used to leave the shop with a full
        # purse and no potions and then die of attrition three rooms later,
        # which measured its shopping rather than the game's difficulty.
        potions = _potion_count(state, content)
        # A full bag makes every purchase fail, and a failed purchase does
        # not advance anything - the bot bought into the same error until the
        # step cap. Vending machines made it reachable, because they stock
        # nothing but healing.
        if potions < 4 and not state.inventory_full():
            slot = _stock_slot(state, content, healing=True)
            if slot:
                return actions.buy(slot)
        roll = rng.randint(1, 100)
        if roll < 20 and state.currency > 400:
            return actions.buy("bag")
        if roll < 45 and not state.inventory_full():
            slot = _stock_slot(state, content, healing=False)
            if slot:
                return actions.buy(slot)
        if roll < 60 and state.inventory and not state.shop.get("machine"):
            junk = _least_useful(state, content)
            if junk:
                return actions.sell(junk)
        return actions.leave_shop()
    if state.mode == MODE_MINIGAME:
        # One branch per game. A game the bot cannot produce valid input for
        # is an infinite loop, not a loss: bad input is rejected without
        # advancing, so the run hangs until the step cap. That is what the
        # Floor 5-8 hosts exposed - before them the bot usually died before
        # meeting a game it did not know.
        game = state.minigame.get("game")
        if game == "dice":
            if state.minigame.get("bid") and rng.chance(0.35):
                return actions.minigame("liar")
            bid = state.minigame.get("bid") or [0, 0]
            return actions.minigame(f"{bid[0] + 1} {rng.randint(1, 6)}")
        if game == "tictactoe":
            return actions.minigame(str(rng.randint(1, 9)))
        if game == "hangman":
            return actions.minigame(rng.choice(list("etaoinshrdlucmfwypvbgkjqxz")))
        if game == "amended":
            return actions.minigame(str(rng.randint(1, 4)))
        if game == "blackjack":
            from engine.minigames.blackjack import value
            # Basic strategy, roughly: hit under 17, stand on 17 or more.
            # Enough to exercise both the win and the bust paths.
            return actions.minigame(
                "hit" if value(state.minigame["player"]) < 17 else "stand")
        return actions.minigame(rng.choice(["rock", "paper", "scissors", "object"]))
    if state.mode == MODE_COMBAT:
        # Drink when hurt. The old bot healed one time in five and the metric
        # was measuring that, not the difficulty.
        if state.player.hp < state.player.hp_max * 0.45:
            potion = _best_potion(state, content)
            if potion:
                return actions.use(potion)
        roll = rng.randint(1, 100)
        if roll < 18 and state.player.abilities:
            return actions.ability(rng.choice(state.player.abilities))
        if roll < 24 and state.player.hp < state.player.hp_max // 4:
            return actions.flee()
        return actions.attack()
    if len(state.inventory) >= state.cap():
        junk = _least_useful(state, content)
        if junk:
            return actions.drop(junk)
    upgrade = _best_upgrade(state, content)
    if upgrade:
        return actions.equip(upgrade)
    room = None
    try:
        room = content.room(state.floor, state.room)
    except Exception:
        pass
    if (room and room.get("kind") == "safe"
            and not state.flags.get(f"rested.{state.room}")
            and state.player.hp < state.player.hp_max * 0.7):
        return actions.rest()
    roll = rng.randint(1, 100)
    if roll < 8:
        return actions.take("")
    if roll < 12:
        return actions.talk()
    if roll < 16:
        return actions.rest()
    if roll < 20:
        return actions.show_map()
    if roll < 23:
        return actions.sheet()
    if roll < 26:
        return actions.inventory()
    # Bias toward unexplored exits, or a pure random walk never finishes a
    # 30-room floor inside any sane turn budget.
    try:
        room = content.room(state.floor, state.room)
        fresh = [d for d, dest in room.get("exits", {}).items()
                 if dest not in state.visited]
        if fresh and rng.chance(0.75):
            return actions.move(fresh[0][0])
        return actions.move(rng.choice(list(room.get("exits", {}))or DIRS)[0])
    except Exception:
        return actions.move(rng.choice(DIRS))


def _scan(events, missing):
    """Catch unresolved theme keys, which render as <<some.key>> on screen.

    The validator can only check keys it knows to look for; this catches any
    key built at runtime, which is how twelve missing event strings shipped.
    """
    # <<rainbow>> is not a key: it is RAINBOW_FROM, the deliberate marker
    # render.py splits Floor 7's reveal on. See frontends/terminal/render.py.
    for event in events:
        for value in event.data.values():
            if isinstance(value, str) and "<<" in value:
                for part in value.split("<<")[1:]:
                    key = part.split(">>")[0]
                    if key != "rainbow":
                        missing.add(key)


def run_one(content, seed, verbose=False):
    rng = Rng(seed ^ 0xABCDEF, 0)
    cls = CLASSES[seed % len(CLASSES)]
    comp = COMPANIONS[(seed // 3) % len(COMPANIONS)]
    state, events = step_mod.new_game(content, seed, "Bot", cls, comp)

    kinds = set(e.kind for e in events)
    missing = set()
    _scan(events, missing)
    for turn in range(30000):
        if state.mode in (MODE_DEAD, MODE_WON):
            break
        action = bot_action(state, content, rng)
        state, events = step_mod.step(state, action, content)
        kinds.update(e.kind for e in events)
        _scan(events, missing)

        # Save round-trip every 40 turns.
        if turn % 40 == 0:
            blob = saves.encode(state)
            reloaded, _ = saves.load_state(blob, content, allow_dead=True)
            assert reloaded.to_dict() == state.to_dict(), "save round-trip mismatch"

    return state, kinds, missing


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    content = load_from_disk(os.path.join(ROOT, "content"))
    outcomes = {"cleared": 0, "died": 0, "timeout": 0}
    all_kinds = set()
    all_missing = set()
    failures = []

    for seed in range(1, runs + 1):
        try:
            state, kinds, missing = run_one(content, seed * 7919)
            all_kinds |= kinds
            all_missing |= missing
            if state.mode == MODE_WON:
                outcomes["cleared"] += 1
            elif state.mode == MODE_DEAD:
                outcomes["died"] += 1
            else:
                outcomes["timeout"] += 1
        except Exception:
            failures.append((seed, traceback.format_exc()))

    print(f"runs: {runs}")
    print(f"outcomes: {outcomes}")
    print(f"event kinds seen: {len(all_kinds)}")
    missing = {"Pounced", "MapShown", "LevelUp", "MinigamePrompt",
               "SafeRoomRested", "ContinueSpent", "FloorCleared"} - all_kinds
    if missing:
        print(f"WARNING never triggered: {sorted(missing)}")
    if all_missing:
        print(f"\nMISSING THEME KEYS ({len(all_missing)}):")
        for key in sorted(all_missing):
            print(f"  <<{key}>>")
        return 1
    if failures:
        print(f"\n{len(failures)} FAILURES")
        for seed, tb in failures[:3]:
            print(f"\n--- seed {seed} ---\n{tb}")
        return 1
    print("no crashes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
