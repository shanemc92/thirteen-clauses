"""Minigames obey the same purity rules as the rest of the engine.

    start(state, content, rng, config) -> mg_state (plain dict, JSON-safe)
    step(state, content, rng, mg_state, action) -> (mg_state, events)
    result(mg_state) -> None while running, else {"won": bool, ...}

Losing a minigame never ends a run. It costs HP, an item, or dignity.
"""

from . import amended, blackjack, dice, hangman, rps, tictactoe

REGISTRY = {
    "rps": rps,
    "dice": dice,
    "hangman": hangman,
    "tictactoe": tictactoe,
    "amended": amended,
    "blackjack": blackjack,
}


def get(game_id):
    return REGISTRY[game_id]
