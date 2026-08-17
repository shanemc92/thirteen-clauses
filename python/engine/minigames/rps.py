"""Rock paper scissors, best of five.

The opponent cheats on round three, visibly. Calling it out is optional; the
Advocate wins the callout automatically because arguing is their whole verb.
"""

from .. import events as ev

THROWS = ("rock", "paper", "scissors")
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def start(state, content, rng, config):
    return {
        "id": "rps",
        "round": 1,
        "player_score": 0,
        "opp_score": 0,
        "cheat_pending": False,
        "cheat_called": False,
        "cheat_happened": False,
        "opponent": config.get("opponent", "the_greeter"),
        "reward": config.get("reward"),
        "penalty": config.get("penalty", 3),
        "name_key": config.get("name_key", "minigame.rps.opponent_name"),
        "cube_edge": bool(
            state.companion.cid
            and content.companions[state.companion.cid].get("minigame_edge")),
        "done": None,
    }


def prompt(state, content, mg):
    lines = [content.t("minigame.rps.header",
                       round=mg["round"], you=mg["player_score"],
                       them=mg["opp_score"],
                       who=content.t(mg.get("name_key",
                                            "minigame.rps.opponent_name")))]
    if mg["cheat_pending"]:
        lines.append(content.t("minigame.rps.cheat_notice"))
        lines.append(content.t("minigame.rps.callout_hint"))
    else:
        lines.append(content.t("minigame.rps.choices"))
    return "\n".join(lines)


def step(state, content, rng, mg, action):
    out = []
    choice = (action.arg or "").lower().strip()

    if mg["cheat_pending"] and choice in ("object", "call", "callout", "cheat"):
        mg["cheat_pending"] = False
        mg["cheat_called"] = True
        if state.player.cls == "advocate":
            out.append(ev.speech(state.player.name,
                                 content.t("minigame.rps.callout_advocate")))
            mg["player_score"] += 1
        else:
            total = rng.d(20) + state.player.mod("cha")
            out.append(ev.dice_rolled("d20", [total - state.player.mod("cha")],
                                      state.player.mod("cha"), total,
                                      "call out the cheat (DC 12)"))
            if total >= 12:
                out.append(ev.plain(content.t("minigame.rps.callout_ok")))
                mg["player_score"] += 1
            else:
                out.append(ev.plain(content.t("minigame.rps.callout_fail")))
                mg["opp_score"] += 1
        mg["round"] += 1
        return _check_done(state, content, mg, out)

    if choice not in THROWS:
        return mg, [ev.error(content.t("minigame.rps.bad_input"))]

    if mg["round"] == 3 and not mg["cheat_happened"]:
        # The Greeter throws whatever beats you, and does not hide it well.
        opp = _counter(choice)
        mg["cheat_happened"] = True
        mg["cheat_pending"] = True
        mg["opp_score"] += 1
        out.append(ev.plain(content.t("minigame.rps.throw",
                                      you=choice, them=opp)))
        out.append(ev.speech(content.t(mg.get("name_key", "minigame.rps.opponent_name")),
                             content.t("minigame.rps.cheat_line")))
        return mg, out

    opp = rng.choice(THROWS)
    if state.companion.cid == "cube":
        # The Cube gives +2 to minigames: reroll a loss once per round.
        if BEATS[opp] == choice and rng.chance(0.5):
            opp = rng.choice(THROWS)
            out.append(ev.plain(content.t("minigame.rps.cube_help")))

    out.append(ev.plain(content.t("minigame.rps.throw", you=choice, them=opp)))
    if choice == opp:
        out.append(ev.plain(content.t("minigame.rps.draw")))
    elif BEATS[choice] == opp:
        mg["player_score"] += 1
        out.append(ev.plain(content.t("minigame.rps.win_round")))
    else:
        mg["opp_score"] += 1
        out.append(ev.plain(content.t("minigame.rps.lose_round")))
    mg["round"] += 1
    return _check_done(state, content, mg, out)


def _counter(choice):
    for throw, beaten in BEATS.items():
        if beaten == choice:
            return throw
    return "rock"


def _check_done(state, content, mg, out):
    if mg["player_score"] >= 3:
        mg["done"] = {"won": True}
    elif mg["opp_score"] >= 3:
        mg["done"] = {"won": False}
    return mg, out


def result(mg):
    return mg.get("done")
