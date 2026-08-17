"""Drive a fixed route with auto-combat, so map and boss output can be eyeballed.

    python3 tests/route.py [seed]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import actions, step as step_mod                  # noqa: E402
from engine.content import load_from_disk                     # noqa: E402
from engine.state import MODE_CHOICE, MODE_COMBAT, MODE_DEAD, MODE_MINIGAME, MODE_WON  # noqa: E402
from frontends.terminal.render import Renderer                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROUTE = ["e", "e", "take", "e", "take", "s", "e", "take", "map",
         "w", "w", "rest", "s", "map", "s", "talk",
         "s", "e", "w", "s", "w", "s", "map", "e", "s", "sheet"]


def drive(state, content, renderer, command):
    action = {"map": actions.show_map(), "take": actions.take(""),
              "rest": actions.rest(), "talk": actions.talk(),
              "sheet": actions.sheet()}.get(command)
    if action is None:
        action = actions.move(command)
    state, events = step_mod.step(state, action, content)
    renderer.render(events)
    return resolve(state, content, renderer)


def resolve(state, content, renderer):
    """Auto-play combat, minigames and prompts until back in explore mode."""
    guard = 0
    while guard < 200:
        guard += 1
        if state.mode == MODE_COMBAT:
            act = actions.attack()
        elif state.mode == MODE_MINIGAME:
            act = actions.minigame("rock")
        elif state.mode == MODE_CHOICE:
            act = actions.Action("Choose", "yes")
        else:
            return state
        state, events = step_mod.step(state, act, content)
        renderer.render(events)
        if state.mode in (MODE_DEAD, MODE_WON):
            return state
    return state


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 99
    content = load_from_disk(os.path.join(ROOT, "content"))
    renderer = Renderer()
    renderer.ansi = False
    state, events = step_mod.new_game(content, seed, "Bud", "vanguard", "grunk")
    renderer.render(events)
    for command in ROUTE:
        if state.mode in (MODE_DEAD, MODE_WON):
            break
        print(f"\n>>> {command}")
        state = drive(state, content, renderer, command)
    print(f"\n[final mode: {state.mode}  room: {state.room}  "
          f"hp: {state.player.hp}/{state.player.hp_max}  lvl: {state.player.level}]")


if __name__ == "__main__":
    main()
