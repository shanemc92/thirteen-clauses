"""Hangman, on the floor where things get cut in half.

The words are all terms from the agreement, and the fiction is that the
severance took some of the letters out. Guess one letter at a time, or the
whole word at once if you are confident.
"""

from .. import events as ev

LIVES = 6


def start(state, content, rng, config):
    words = content.raw(config.get("words_key", "minigame.hangman.words")) or []
    word = (rng.choice(words) or "SEVERABILITY").upper()
    return {
        "id": "hangman",
        "word": word,
        "found": sorted({c for c in word if not c.isalpha()}),
        "wrong": [],
        "lives": LIVES,
        "reward": config.get("reward"),
        "penalty": config.get("penalty", 6),
        "name_key": config.get("name_key", "minigame.hangman.opponent_name"),
        "done": None,
    }


def _masked(mg):
    return " ".join(c if (not c.isalpha() or c in mg["found"]) else "_"
                    for c in mg["word"])


def prompt(state, content, mg):
    lines = [content.t("minigame.hangman.header",
                       lives=mg["lives"], total=LIVES)]
    lines.append("  " + _masked(mg))
    if mg["wrong"]:
        lines.append(content.t("minigame.hangman.wrong",
                               letters=" ".join(mg["wrong"])))
    lines.append(content.t("minigame.hangman.how"))
    return "\n".join(lines)


def step(state, content, rng, mg, action):
    out = []
    guess = (action.arg or "").strip().upper()
    if not guess.isalpha():
        return mg, [ev.error(content.t("minigame.hangman.bad_input"))]

    # A whole-word guess.
    if len(guess) > 1:
        if guess == mg["word"]:
            mg["found"] = sorted(set(mg["word"]))
            out.append(ev.plain(content.t("minigame.hangman.solved")))
            mg["done"] = {"won": True}
        else:
            mg["lives"] -= 2
            out.append(ev.plain(content.t("minigame.hangman.wrong_word",
                                          guess=guess)))
            if mg["lives"] <= 0:
                mg["done"] = {"won": False}
                out.append(ev.plain(content.t("minigame.hangman.reveal",
                                              word=mg["word"])))
        return mg, out

    if guess in mg["found"] or guess in mg["wrong"]:
        return mg, [ev.error(content.t("minigame.hangman.already", letter=guess))]

    if guess in mg["word"]:
        mg["found"] = sorted(set(mg["found"]) | {guess})
        count = mg["word"].count(guess)
        out.append(ev.plain(content.t("minigame.hangman.hit",
                                      letter=guess, count=count)))
        if all(c in mg["found"] for c in mg["word"] if c.isalpha()):
            out.append(ev.plain(content.t("minigame.hangman.solved")))
            mg["done"] = {"won": True}
    else:
        mg["wrong"] = sorted(set(mg["wrong"]) | {guess})
        mg["lives"] -= 1
        out.append(ev.plain(content.t("minigame.hangman.miss", letter=guess)))
        if mg["lives"] <= 0:
            mg["done"] = {"won": False}
            out.append(ev.plain(content.t("minigame.hangman.reveal",
                                          word=mg["word"])))
    return mg, out


def result(mg):
    return mg.get("done")
