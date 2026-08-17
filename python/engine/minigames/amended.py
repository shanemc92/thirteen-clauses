"""Rock-paper-scissors, amended twice.

Clause 11 can change the terms at any time, so it added two more throws. The
five are themed: CLAUSE, AMENDMENT, SCHEDULE, PRECEDENT, WAIVER. Each beats
two of the others, exactly as lizard-Spock does.

    CLAUSE     beats SCHEDULE, WAIVER
    AMENDMENT  beats CLAUSE, PRECEDENT
    SCHEDULE   beats AMENDMENT, WAIVER
    PRECEDENT  beats CLAUSE, SCHEDULE
    WAIVER     beats AMENDMENT, PRECEDENT
"""

from .. import events as ev

THROWS = ("clause", "amendment", "schedule", "precedent", "waiver")
BEATS = {
    "clause": ("schedule", "waiver"),
    "amendment": ("clause", "precedent"),
    "schedule": ("amendment", "waiver"),
    "precedent": ("clause", "schedule"),
    "waiver": ("amendment", "precedent"),
}
WIN_AT = 3


def start(state, content, rng, config):
    return {
        "id": "amended",
        "round": 1,
        "player_score": 0,
        "opp_score": 0,
        "amended": False,
        "reward": config.get("reward"),
        "penalty": config.get("penalty", 6),
        "name_key": config.get("name_key", "minigame.amended.opponent_name"),
        "done": None,
    }


def prompt(state, content, mg):
    lines = [content.t("minigame.amended.header",
                       round=mg["round"], you=mg["player_score"],
                       them=mg["opp_score"],
                       who=content.t(mg["name_key"]))]
    lines.append(content.t("minigame.amended.choices"))
    lines.append(content.t("minigame.amended.table"))
    return "\n".join(lines)


def step(state, content, rng, mg, action):
    out = []
    choice = (action.arg or "").strip().lower()
    if choice not in THROWS:
        return mg, [ev.error(content.t("minigame.amended.bad_input"))]

    opp = rng.choice(THROWS)

    # Once per game the terms are amended mid-throw, which is the joke and
    # also the one thing the player can do nothing about.
    if mg["round"] == 3 and not mg["amended"]:
        mg["amended"] = True
        out.append(ev.speech(content.t(mg["name_key"]),
                             content.t("minigame.amended.amendment")))
        opp = next(t for t in THROWS if choice in BEATS[t])

    out.append(ev.plain(content.t("minigame.amended.throw",
                                  you=choice.upper(), them=opp.upper())))
    if choice == opp:
        out.append(ev.plain(content.t("minigame.amended.draw")))
    elif opp in BEATS[choice]:
        mg["player_score"] += 1
        out.append(ev.plain(content.t(f"minigame.amended.beats.{choice}",
                                      rng, other=opp.upper())))
    else:
        mg["opp_score"] += 1
        out.append(ev.plain(content.t(f"minigame.amended.beats.{opp}",
                                      rng, other=choice.upper())))

    mg["round"] += 1
    if mg["player_score"] >= WIN_AT:
        mg["done"] = {"won": True}
    elif mg["opp_score"] >= WIN_AT:
        mg["done"] = {"won": False}
    return mg, out


def result(mg):
    return mg.get("done")
