"""Twenty-one, best of three hands.

Replaces `precedent`, which asked you to memorise a sequence that was still
sitting on the screen above you: in a terminal with scrollback, a memory game
tests nothing at all.

This one survives being visible, which is the point. Everything is face up
except the dealer's hole card, and the only thing you have is the decision.

A real 52-card shoe rather than random ranks, so what has already gone matters
and the Cube's peek is worth something.
"""

from .. import events as ev

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["S", "H", "D", "C"]
HANDS_TO_WIN = 2
DEALER_STANDS = 17
RESHUFFLE_AT = 15


def start(state, content, rng, config):
    mg = {
        "id": "blackjack",
        "hand": 1,
        "player_score": 0,
        "opp_score": 0,
        "shoe": _shuffled(rng),
        "player": [],
        "dealer": [],
        "stood": False,
        "settled": False,
        "reward": config.get("reward"),
        "penalty": config.get("penalty", 6),
        "xp": config.get("xp", 40),
        "name_key": config.get("name_key", "minigame.blackjack.opponent_name"),
        "cube_edge": bool(
            state.companion.cid
            and content.companions[state.companion.cid].get("minigame_edge")),
        "done": None,
    }
    _deal(mg, rng)
    return mg


def _shuffled(rng, exclude=()):
    """A fresh shoe, minus anything currently face up on the table.

    `exclude` matters on a mid-hand reshuffle: without it the new shoe
    contained the cards already in the two hands, so the same card could be
    dealt twice in one hand — and counting what had gone, which is the whole
    point of a real shoe and of the Cube's peek, stopped meaning anything.
    """
    held = set(exclude)
    deck = [r + s for s in SUITS for r in RANKS if r + s not in held]
    # Fisher-Yates through the engine's own rng, so a seed replays exactly.
    for i in range(len(deck) - 1, 0, -1):
        j = rng.randint(0, i)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def _draw(mg, rng):
    if len(mg["shoe"]) < RESHUFFLE_AT:
        mg["shoe"] = _shuffled(rng, mg.get("player", ()) + mg.get("dealer", ()))
    return mg["shoe"].pop()


def _deal(mg, rng):
    # Cleared first so the last hand counts as discarded: on a reshuffle
    # those cards belong back in the shoe, and only what is face up now is
    # held out of it.
    mg["player"] = []
    mg["dealer"] = []
    mg["player"] = [_draw(mg, rng), _draw(mg, rng)]
    mg["dealer"] = [_draw(mg, rng), _draw(mg, rng)]
    mg["stood"] = False
    mg["settled"] = False


def value(cards):
    """Best total that is not a bust. Aces are 11 until they cannot be."""
    total = 0
    aces = 0
    for card in cards:
        rank = card[:-1]
        if rank == "A":
            aces += 1
            total += 11
        elif rank in ("10", "J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _show(cards):
    return " ".join(cards)


def prompt(state, content, mg):
    who = content.t(mg.get("name_key", "minigame.blackjack.opponent_name"))
    lines = [content.t("minigame.blackjack.header",
                       hand=mg["hand"], you=mg["player_score"],
                       them=mg["opp_score"], who=who)]
    if mg["stood"] or mg["settled"]:
        lines.append(content.t("minigame.blackjack.dealer_full",
                               cards=_show(mg["dealer"]),
                               total=value(mg["dealer"])))
    else:
        lines.append(content.t("minigame.blackjack.dealer_up",
                               card=mg["dealer"][0]))
        if mg["cube_edge"]:
            lines.append(content.t("minigame.blackjack.cube_help",
                                   card=mg["dealer"][1]))
    lines.append(content.t("minigame.blackjack.your_hand",
                           cards=_show(mg["player"]),
                           total=value(mg["player"])))
    lines.append(content.t("minigame.blackjack.hint"))
    return "\n".join(lines)


def step(state, content, rng, mg, action):
    out = []
    choice = (action.arg or "").strip().lower()

    if choice in ("hit", "h", "twist", "card"):
        mg["player"].append(_draw(mg, rng))
        total = value(mg["player"])
        out.append(ev.plain(content.t("minigame.blackjack.drew",
                                      card=mg["player"][-1], total=total)))
        if total > 21:
            out.append(ev.plain(content.t("minigame.blackjack.bust",
                                          total=total)))
            return _settle(state, content, rng, mg, won=False, out=out)
        return mg, out

    if choice in ("stand", "s", "stick", "hold"):
        mg["stood"] = True
        return _dealer_turn(state, content, rng, mg, out)

    return mg, [ev.error(content.t("minigame.blackjack.bad_input"))]


def _dealer_turn(state, content, rng, mg, out):
    out.append(ev.plain(content.t("minigame.blackjack.reveal",
                                  card=mg["dealer"][1])))
    while value(mg["dealer"]) < DEALER_STANDS:
        mg["dealer"].append(_draw(mg, rng))
        out.append(ev.plain(content.t("minigame.blackjack.dealer_draws",
                                      card=mg["dealer"][-1],
                                      total=value(mg["dealer"]))))
    them = value(mg["dealer"])
    you = value(mg["player"])
    if them > 21:
        out.append(ev.plain(content.t("minigame.blackjack.dealer_bust",
                                      total=them)))
        return _settle(state, content, rng, mg, won=True, out=out)
    if them == you:
        out.append(ev.plain(content.t("minigame.blackjack.push", total=you)))
        return _settle(state, content, rng, mg, won=None, out=out)
    won = you > them
    out.append(ev.plain(content.t(
        "minigame.blackjack.win_hand" if won else "minigame.blackjack.lose_hand",
        you=you, them=them)))
    return _settle(state, content, rng, mg, won=won, out=out)


def _settle(state, content, rng, mg, won, out):
    """Score the hand and deal the next, unless the match is over.

    A push is dealt again without scoring, which is why `won` is tri-state.
    """
    if won is True:
        mg["player_score"] += 1
    elif won is False:
        mg["opp_score"] += 1

    if mg["player_score"] >= HANDS_TO_WIN:
        mg["done"] = {"won": True}
        return mg, out
    if mg["opp_score"] >= HANDS_TO_WIN:
        mg["done"] = {"won": False}
        return mg, out

    if won is not None:
        mg["hand"] += 1
    _deal(mg, rng)
    out.append(ev.plain(content.t("minigame.blackjack.next_hand")))
    return mg, out


def result(mg):
    return mg.get("done")
