"""Line editing, history and tab completion.

Uses the stdlib readline where it exists, which gives arrow-key history,
backspace, and Ctrl-A/E/W editing for free. Where it does not (some Android
Python builds ship without it), everything degrades to plain input() and the
game still plays.
"""

import atexit
import os

from frontends.terminal.effects import notify_vocab

try:
    import readline
    HAVE_READLINE = True
except ImportError:          # some Android / minimal builds
    readline = None
    HAVE_READLINE = False

VERBS = [
    "north", "south", "east", "west", "look", "map", "sheet", "char",
    "inventory", "take", "talk", "rest", "use", "equip", "drop", "attack",
    "flee", "wait", "combat", "pace", "width", "save", "load", "read",
    "record",
    "buy", "sell", "leave", "help", "quit",
]

# ABILITY takes an ability name, so it completes against what you know.
ABILITY_VERBS = {"ability", "ab", "cast"}
PACE_ARGS = ["fast", "slow", "manual"]

# Verbs that take an inventory item as their argument.
ITEM_VERBS = {"use", "equip", "drop"}


class Completer:
    """Completes verbs first, then whatever that verb takes as an argument.

    The candidate list is rebuilt from live state before every prompt, so
    picking up a ration makes it tab-completable immediately.
    """

    def __init__(self):
        self.in_minigame = False
        self.items = []
        self.abilities = []
        self.enemies = []
        self.saves = []
        self._matches = []

    def refresh(self, state, content, save_names):
        from engine.state import MODE_MINIGAME
        # A minigame owns the command space, so completing verbs into it would
        # be offering things that no longer work.
        self.in_minigame = state.mode == MODE_MINIGAME
        self.items = sorted({
            content.t(content.item(entry["id"])["name_key"]).lower()
            for entry in state.inventory
        })
        self.abilities = sorted(
            content.t(content.ability(aid)["name_key"]).lower()
            for aid in state.player.abilities
        )
        self.enemies = []
        if state.combat:
            self.enemies = [e.name.lower() for e in state.combat.living()]
        self.saves = sorted(save_names)

    def snapshot(self):
        """The candidate sets, for a frontend that cannot use readline.

        Pyodide has no tty, so stdin never reaches readline and the browser
        has to do its own completion. Rather than duplicate the vocabulary
        there, publish it: the page keeps the switch in `candidates` in sync
        and gets the words from here.
        """
        if self.in_minigame:
            # Published as empty rather than withheld, so the page clears the
            # vocabulary it was holding instead of completing stale verbs.
            return {key: [] for key in (
                "verbs", "items", "abilities", "enemies", "saves",
                "pace", "item_verbs", "ability_verbs")}
        return {
            "verbs": sorted(set(VERBS) | ABILITY_VERBS),
            "items": self.items,
            "abilities": self.abilities,
            "enemies": self.enemies,
            "saves": self.saves,
            "pace": PACE_ARGS,
            "item_verbs": sorted(ITEM_VERBS),
            "ability_verbs": sorted(ABILITY_VERBS),
        }

    def candidates(self, line, word):
        if self.in_minigame:
            return []
        parts = line.split()
        first = parts[0].lower() if parts else ""
        typing_first = len(parts) == 0 or (len(parts) == 1 and not line.endswith(" "))

        if typing_first:
            return VERBS + self.abilities

        if first == "sell":
            return self.items
        if first in ITEM_VERBS:
            return self.items
        if first == "pace":
            return PACE_ARGS
        if first == "load":
            return self.saves
        if first in ("buy", "b"):
            return ["bag"]
        if first in ABILITY_VERBS:
            return self.abilities
        if first in ("attack", "a", "hit"):
            return self.enemies
        return []

    def complete(self, word, index):
        if index == 0:
            line = readline.get_line_buffer()[:readline.get_endidx()]
            pool = self.candidates(line, word)
            low = word.lower()
            self._matches = sorted({c for c in pool if c.startswith(low)})
            if not self._matches:
                # Fall back to substring, so "coffee" finds "vending machine coffee".
                self._matches = sorted({c for c in pool if low and low in c})
        return self._matches[index] if index < len(self._matches) else None


class Input:
    def __init__(self, history_path=None):
        self.completer = Completer()
        self.enabled = HAVE_READLINE
        if not self.enabled:
            return
        readline.set_completer(self.completer.complete)
        # Space is not a delimiter: item names have spaces in them, and we want
        # "vending machine cof<tab>" to complete rather than treating each word
        # as a separate token.
        readline.set_completer_delims("")
        for binding in ("tab: complete", "set editing-mode emacs",
                        "set horizontal-scroll-mode on"):
            try:
                readline.parse_and_bind(binding)
            except Exception:
                pass
        if history_path:
            try:
                readline.read_history_file(history_path)
            except (FileNotFoundError, OSError):
                pass
            readline.set_history_length(300)
            atexit.register(self._save_history, history_path)

    @staticmethod
    def _save_history(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            readline.write_history_file(path)
        except (OSError, AttributeError):
            pass

    def refresh(self, state, content, save_names=()):
        """Rebuild the candidate list and publish it to the page.

        Not gated on readline: the browser has no tty, so `enabled` is False
        there, but that is exactly where the published snapshot is needed.
        """
        self.completer.refresh(state, content, save_names)
        notify_vocab(self.completer.snapshot())

    def ask(self, prompt):
        return input(prompt)

    @property
    def note(self):
        # The browser completes on the page rather than through readline
        # (no tty in Pyodide), so it earns the same line the terminal gets.
        from frontends.terminal.effects import IN_BROWSER
        if self.enabled or IN_BROWSER:
            return "  Tab completes. Up arrow repeats. Type HELP for commands."
        return "  Type HELP for commands."
