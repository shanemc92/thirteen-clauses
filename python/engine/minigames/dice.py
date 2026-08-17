"""Liar's Dice against the Coin Diver.

Five dice each, hidden. Players alternate raising a bid of the form
"N dice showing V or better", or calling the last bid a lie. Calling correctly
wins the round; calling wrong loses it. First to two round wins takes it.

Simplified from the pub version: ones are not wild, and bids compare on
quantity first, then face. It fits in a phone terminal and it is still a bluff.
"""

from .. import events as ev

FACES = 6
DICE = 5
WIN_ROUNDS = 2


def start(state, content, rng, config):
    mg = {
        "id": "dice",
        "round": 1,
        "player_score": 0,
        "opp_score": 0,
        "opponent": config.get("opponent", "coin_diver"),
        "reward": config.get("reward"),
        "penalty": config.get("penalty", 4),
        "name_key": config.get("name_key", "minigame.dice.opponent_name"),
        "cube_edge": bool(
            state.companion.cid
            and content.companions[state.companion.cid].get("minigame_edge")),
        "done": None,
    }
    _deal(mg, rng)
    return mg


def _deal(mg, rng):
    mg["player_dice"] = sorted(rng.d(FACES) for _ in range(DICE))
    mg["opp_dice"] = sorted(rng.d(FACES) for _ in range(DICE))
    mg["bid"] = None          # [quantity, face]
    mg["bidder"] = None
    mg["turn"] = "player"


def prompt(state, content, mg):
    if mg.get("cube_edge") and mg.get("opp_dice"):
        peek = sorted(mg["opp_dice"])[len(mg["opp_dice"]) // 2]
        return "\n".join([
            content.t("minigame.dice.cube_help", face=peek),
            _prompt_body(state, content, mg)])
    return _prompt_body(state, content, mg)


def _prompt_body(state, content, mg):
    lines = [content.t("minigame.dice.header",
                       round=mg["round"], you=mg["player_score"],
                       them=mg["opp_score"],
                       who=content.t(mg.get("name_key",
                                            "minigame.dice.opponent_name")))]
    lines.append(content.t("minigame.dice.your_dice",
                           dice=" ".join(str(d) for d in mg["player_dice"])))
    if mg["bid"]:
        lines.append(content.t("minigame.dice.current_bid",
                               n=mg["bid"][0], face=mg["bid"][1]))
        lines.append(content.t("minigame.dice.options"))
    else:
        lines.append(content.t("minigame.dice.open"))
    return "\n".join(lines)


def _count(dice, face):
    return sum(1 for d in dice if d >= face)


def _parse_bid(text):
    parts = text.replace(",", " ").split()
    nums = [int(p) for p in parts if p.isdigit()]
    if len(nums) >= 2 and 1 <= nums[0] <= DICE * 2 and 1 <= nums[1] <= FACES:
        return [nums[0], nums[1]]
    return None


def _higher(bid, previous):
    if previous is None:
        return True
    if bid[0] != previous[0]:
        return bid[0] > previous[0]
    return bid[1] > previous[1]


def step(state, content, rng, mg, action):
    out = []
    text = (action.arg or "").lower().strip()

    if text in ("liar", "call", "bluff") and mg["bid"]:
        return _call(state, content, rng, mg, out, caller="player")

    bid = _parse_bid(text)
    if bid is None:
        return mg, [ev.error(content.t("minigame.dice.bad_input"))]
    if not _higher(bid, mg["bid"]):
        return mg, [ev.error(content.t("minigame.dice.too_low"))]

    mg["bid"] = bid
    mg["bidder"] = "player"
    out.append(ev.plain(content.t("minigame.dice.you_bid",
                                  n=bid[0], face=bid[1])))
    return _opponent_turn(state, content, rng, mg, out)


def _opponent_turn(state, content, rng, mg, out):
    bid = mg["bid"]
    have = _count(mg["opp_dice"], bid[1])
    # He estimates the other five dice, then leans on the estimate. He is a
    # gambler, not a statistician, and the tell is that he always leans high.
    expected = have + (DICE * (FACES - bid[1] + 1)) // FACES
    confidence = expected - bid[0]

    if confidence < -1 and rng.chance(0.75):
        return _call(state, content, rng, mg, out, caller="opponent")

    raise_face = bid[1] + 1 if bid[1] < FACES and rng.chance(0.4) else bid[1]
    raise_n = bid[0] if raise_face > bid[1] else bid[0] + 1
    if raise_n > DICE * 2:
        return _call(state, content, rng, mg, out, caller="opponent")

    mg["bid"] = [raise_n, raise_face]
    mg["bidder"] = "opponent"
    out.append(ev.speech(content.t(mg.get("name_key", "minigame.dice.opponent_name")),
                         content.t("minigame.dice.he_bids", rng,
                                   n=raise_n, face=raise_face)))
    return mg, out


def _call(state, content, rng, mg, out, caller):
    bid = mg["bid"]
    total = _count(mg["player_dice"], bid[1]) + _count(mg["opp_dice"], bid[1])
    out.append(ev.plain(content.t("minigame.dice.reveal",
                                  yours=" ".join(str(d) for d in mg["player_dice"]),
                                  his=" ".join(str(d) for d in mg["opp_dice"]),
                                  n=total, face=bid[1])))
    bid_stands = total >= bid[0]

    if caller == "player":
        out.append(ev.plain(content.t("minigame.dice.you_call")))
        player_wins = not bid_stands
    else:
        out.append(ev.speech(content.t(mg.get("name_key", "minigame.dice.opponent_name")),
                             content.t("minigame.dice.he_calls")))
        player_wins = bid_stands

    if state.companion.cid == "cube" and not player_wins and rng.chance(0.3):
        out.append(ev.plain(content.t("minigame.dice.cube_help")))
        player_wins = True

    if player_wins:
        mg["player_score"] += 1
        out.append(ev.plain(content.t("minigame.dice.win_round")))
    else:
        mg["opp_score"] += 1
        out.append(ev.plain(content.t("minigame.dice.lose_round")))

    if mg["player_score"] >= WIN_ROUNDS:
        mg["done"] = {"won": True}
    elif mg["opp_score"] >= WIN_ROUNDS:
        mg["done"] = {"won": False}
    else:
        mg["round"] += 1
        _deal(mg, rng)
    return mg, out


def result(mg):
    return mg.get("done")
