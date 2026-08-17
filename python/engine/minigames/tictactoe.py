"""Noughts and crosses, played on the court's own nine-box docket.

Pick a box 1-9. The opponent takes a win if it has one, blocks yours if you
have one, and otherwise prefers the centre, then corners.

It is deliberately deterministic, which makes it a puzzle rather than a
gamble: going second it holds a draw against anything except one exact
sequence, and that sequence works every single time once you know it. A memo
on Floor 8 has it written on a locker door, because a player who has not
found the memo would otherwise never win - measured, a sensible player drew
100% of the time.

Draws count towards MAX_ROUNDS so the docket always closes; without that a
drawing player was stuck in a match that could not end.
""" 

from .. import events as ev

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8),
         (0, 3, 6), (1, 4, 7), (2, 5, 8),
         (0, 4, 8), (2, 4, 6)]
ROUNDS_TO_WIN = 2
MAX_ROUNDS = 7          # draws included, so a run of them cannot go forever


def start(state, content, rng, config):
    return {
        "id": "tictactoe",
        "board": [" "] * 9,
        "round": 1,
        "player_score": 0,
        "opp_score": 0,
        "reward": config.get("reward"),
        "penalty": config.get("penalty", 6),
        "name_key": config.get("name_key", "minigame.tictactoe.opponent_name"),
        "done": None,
    }


def _winner(board):
    for a, b, c in LINES:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    return None


def _full(board):
    return all(cell != " " for cell in board)


def _best(board, mark, other):
    """Win, else block, else the best square going. Deterministic on purpose:
    the fork in the Floor 8 memo has to work every time it is used."""
    for target in (mark, other):            # win, else block
        for i in range(9):
            if board[i] == " ":
                board[i] = target
                won = _winner(board) == target
                board[i] = " "
                if won:
                    return i

    for i in (4, 0, 2, 6, 8, 1, 3, 5, 7):
        if board[i] == " ":
            return i
    return None


def prompt(state, content, mg):
    board = mg["board"]
    cells = [board[i] if board[i] != " " else str(i + 1) for i in range(9)]
    grid = "\n".join([
        content.t("minigame.tictactoe.header",
                  round=mg["round"], you=mg["player_score"],
                  them=mg["opp_score"]),
        f"   {cells[0]} | {cells[1]} | {cells[2]}",
        "  ---+---+---",
        f"   {cells[3]} | {cells[4]} | {cells[5]}",
        "  ---+---+---",
        f"   {cells[6]} | {cells[7]} | {cells[8]}",
        content.t("minigame.tictactoe.how"),
    ])
    return grid


def _end_round(state, content, mg, out, outcome):
    if outcome == "player":
        mg["player_score"] += 1
        out.append(ev.plain(content.t("minigame.tictactoe.win_round")))
    elif outcome == "opponent":
        mg["opp_score"] += 1
        out.append(ev.plain(content.t("minigame.tictactoe.lose_round")))
    else:
        out.append(ev.plain(content.t("minigame.tictactoe.draw")))

    if mg["player_score"] >= ROUNDS_TO_WIN:
        mg["done"] = {"won": True}
    elif mg["opp_score"] >= ROUNDS_TO_WIN:
        mg["done"] = {"won": False}
    elif mg["round"] >= MAX_ROUNDS:
        # The docket closes whatever the score. Level means the house keeps
        # it, which is the only reading of a draw this building allows.
        won = mg["player_score"] > mg["opp_score"]
        out.append(ev.plain(content.t("minigame.tictactoe.closed",
                                      you=mg["player_score"],
                                      them=mg["opp_score"])))
        mg["done"] = {"won": won}
    else:
        mg["round"] += 1
        mg["board"] = [" "] * 9
    return mg, out


def step(state, content, rng, mg, action):
    out = []
    raw = (action.arg or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 9:
        return mg, [ev.error(content.t("minigame.tictactoe.bad_input"))]
    box = int(raw) - 1
    if mg["board"][box] != " ":
        return mg, [ev.error(content.t("minigame.tictactoe.taken"))]

    mg["board"][box] = "X"
    out.append(ev.plain(content.t("minigame.tictactoe.you_play", box=box + 1)))
    if _winner(mg["board"]) == "X":
        return _end_round(state, content, mg, out, "player")
    if _full(mg["board"]):
        return _end_round(state, content, mg, out, "draw")

    reply = _best(mg["board"], "O", "X")
    mg["board"][reply] = "O"
    out.append(ev.speech(content.t(mg["name_key"]),
                         content.t("minigame.tictactoe.they_play", rng,
                                   box=reply + 1)))
    if _winner(mg["board"]) == "O":
        return _end_round(state, content, mg, out, "opponent")
    if _full(mg["board"]):
        return _end_round(state, content, mg, out, "draw")
    return mg, out


def result(mg):
    return mg.get("done")
