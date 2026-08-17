"""Terminal renderer.

Consumes events. Never reads GameState. Owns every glyph, every line break,
and the colour capability that Floor 7 flips on.

Formatting rules, applied everywhere:
  * Prose is flush left. No indent, so a wrapped line looks like the line
    above it rather than a different kind of thing.
  * Only secondary detail (an item's description under its name, an ability's
    description under its name) is indented, by two.
  * ASCII art is always flush left too. Indenting it only when it happens to
    fit was the whole cause of the art looking ragged.
"""

import os
import shutil
import sys
import textwrap
import time

from engine.content import title_case
from frontends.terminal.effects import Effects

# A single block longer than this earns a press-Enter stop on its own. It
# used to be 7, which at 50 columns is an ordinary paragraph: a floor clear
# asked for six presses. Deliberate beats come from an explicit Pause event
# instead, so this only has to catch a genuine wall of text.
GATE_LINES = 22


def detect_width():
    """Terminal width, overridable.

    Phone terminals (Pydroid, Termux) often report 80 while showing far less,
    which shreds ASCII art. THIRTEEN_WIDTH or --width wins over detection.
    """
    override = os.environ.get("THIRTEEN_WIDTH")
    if override and override.isdigit():
        return max(32, min(int(override), 120))
    return max(32, min(shutil.get_terminal_size((80, 24)).columns, 92))


WIDTH = detect_width()

C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "red": "\033[38;5;203m", "green": "\033[38;5;114m",
    "yellow": "\033[38;5;222m", "blue": "\033[38;5;111m",
    "magenta": "\033[38;5;176m", "cyan": "\033[38;5;80m",
    "grey": "\033[38;5;245m", "white": "\033[38;5;255m",
    # The thing that answers empty rooms. Deep purple, so it is never
    # mistaken for the narrator's grey - they are different things and only
    # one of them is on your side. BD93F9 in 24-bit; TRUECOLOUR picks the
    # nearest 256-colour index instead when the terminal cannot do better.
    "purple": "\033[38;2;189;147;249m",
    "purple256": "\033[38;5;141m",
}


# A full spectrum in 256-colour codes, red round to magenta and back.
RAINBOW = [196, 202, 208, 214, 220, 226, 190, 154, 118, 82, 46, 48, 51,
           45, 39, 33, 27, 63, 99, 129, 165, 201, 200, 199, 198, 197]

# Text after this marker starts the spectrum. Everything before it renders
# normally, so a reveal can land mid-paragraph on the exact line it belongs to.
RAINBOW_FROM = "<<rainbow>>"

# 24-bit colour is widely but not universally supported. COLORTERM is the
# conventional signal; Pyodide's xterm.js host does it too but sets nothing,
# so the browser is opted in explicitly. Anywhere unsure gets the 256-colour
# approximation, which is close enough that nobody but a colourimeter minds.
TRUECOLOUR = (os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
              or sys.platform == "emscripten")


# The record, the inventory, the shop and the sheet all label items the same
# way. One implementation, in the engine, so they cannot drift apart.
_title_case = title_case



class Renderer:
    def __init__(self, width=None, pace="slow"):
        self.colour = False          # Floors 1-6 are strictly monochrome
        self.rainbow = False         # Floor 7 only
        self.ansi = os.environ.get("TERM", "") not in ("", "dumb")
        self.width = width or WIDTH
        self.pace = pace             # fast | slow | manual
        self.cps = 0                 # >0 means prose arrives a character at a time
        self._hue = 0
        self.content = None          # set by the frontend, for still frames
        # Armed by _mark(), called from every primitive that prints. A gate
        # is a no-op until something has been shown since the last one, or a
        # self-gating narration followed by an explicit Pause asks twice for
        # one beat.
        self._printed_since_gate = True
        self.fx = Effects(self)

    def content_art(self, key):
        return self.content.get_art(key) if self.content else ""

    def set_palette(self, palette):
        self.colour = palette not in ("mono", "none")
        self.rainbow = palette == "rainbow"

    # -- primitives ------------------------------------------------------
    def _c(self, text, name):
        if not (self.colour and self.ansi):
            return text
        return f"{C[name]}{text}{C['reset']}"

    def _paint(self, text):
        """Run a spectrum through a line of text, one step per character.

        Spaces are left uncoloured: it looks the same and roughly halves the
        escape codes, which matters on a phone terminal.
        """
        if not (self.rainbow and self.ansi):
            return text
        out = []
        for char in text:
            if char == " ":
                out.append(char)
                continue
            out.append(f"\033[38;5;{RAINBOW[self._hue % len(RAINBOW)]}m{char}")
            self._hue += 1
        self._hue += 2          # nudge the start of the next line along
        return "".join(out) + C["reset"]

    def _style(self, text, name):
        """Dim and bold work in monochrome; they are weight, not colour."""
        if not self.ansi:
            return text
        # 24-bit is not universal. Anything with a `<name>256` companion in C
        # falls back to it unless the terminal advertises truecolour.
        if not TRUECOLOUR and (name + "256") in C:
            name += "256"
        return f"{C[name]}{text}{C['reset']}"

    def wrap(self, text, indent="", hanging=None, paint=False, colour=None):
        """Print wrapped text. `hanging` indents continuation lines only.

        `colour` is applied per line AFTER wrapping, never before. ANSI
        escapes are characters as far as textwrap is concerned, so colouring
        first cost nine columns of width and broke every error message about
        a word and a half early.

        Returns the number of lines printed, so callers can gate on length.
        """
        text = str(text)
        if RAINBOW_FROM in text:
            before, after = text.split(RAINBOW_FROM, 1)
            count = self.wrap(before.rstrip("\n"), indent, hanging,
                              paint=False, colour=colour)
            if after.strip():
                print()
                count += 1 + self.wrap(after.lstrip("\n"), indent, hanging,
                                       paint=self.rainbow, colour=colour)
            return count

        count = 0
        for para in text.split("\n"):
            if not para.strip():
                print()
                count += 1
                continue
            # A line that arrives already indented keeps that indent on its
            # continuations. textwrap only ever saw the indent as part of the
            # first line's text, so anything long enough to wrap came out
            # crooked: indented at the top, flush underneath.
            own = para[:len(para) - len(para.lstrip(" "))]
            para = para.lstrip(" ")
            block = textwrap.fill(
                para, self.width, initial_indent=indent + own,
                subsequent_indent=(indent + own) if hanging is None
                else hanging + own)
            if paint:
                block = "\n".join(self._paint(line)
                                   for line in block.split("\n"))
            elif colour:
                block = "\n".join(self._c(line, colour)
                                  for line in block.split("\n"))
            self._emit(block)
            count += block.count("\n") + 1
        return count

    def _emit(self, block):
        """Print a block, a character at a time where the floor asks for it."""
        self._mark()
        from frontends.terminal.effects import IN_BROWSER, NO_ANIM
        if (not self.cps or self.pace == "fast" or not self.ansi
                or IN_BROWSER or NO_ANIM):
            print(block)
            return
        delay = 1.0 / max(1, self.cps)
        try:
            for char in block:
                sys.stdout.write(char)
                sys.stdout.flush()
                if char not in " \n":
                    time.sleep(delay)
            print()
        except KeyboardInterrupt:
            print(block[block.rfind("\n") + 1:])
            self.cps = 0          # they have had enough of that

    def rule(self, char="-"):
        self._mark()
        print(self._style(char * self.width, "dim"))

    def art(self, text):
        """Flush left, always. Consistency beats centring."""
        self._mark()
        for line in str(text).split("\n"):
            line = line.rstrip()
            print(self._paint(line) if self.rainbow else line)

    def bar(self, current, maximum, width=20):
        if maximum <= 0:
            return "[]"
        filled = max(0, min(width, round(width * current / maximum)))
        return "[" + "#" * filled + "-" * (width - filled) + "]"

    # -- pacing ----------------------------------------------------------
    def gate(self, prompt=None):
        """Hard stop: press Enter. Skipped entirely on `pace fast`.

        Two gates with nothing printed between them is two presses for one
        beat, which is what a long narration followed by an explicit Pause
        used to give you on every floor clear. A gate is therefore a no-op
        until something has been printed since the last one.
        """
        if self.pace == "fast" or not self._printed_since_gate:
            return
        self._printed_since_gate = False
        try:
            input(self._style(prompt or "[enter]", "dim"))
        except (EOFError, KeyboardInterrupt):
            self.pace = "fast"

    def _mark(self):
        """Something has been shown, so the next gate is worth a press.

        Called from the primitives rather than from render(), because the
        whole intro - the briefing, the class and companion pickers - prints
        through wrap() and art() without going near an event. Arming only in
        render() meant that after the intro's first press the flag latched
        off and the remaining five briefing sections scrolled past together.
        """
        self._printed_since_gate = True

    def gate_if_long(self, lines):
        if lines >= GATE_LINES:
            self.gate()

    def _pause(self, prompt="..."):
        if self.pace == "fast":
            return
        if self.pace == "manual":
            self.gate(prompt)
            return
        try:
            time.sleep(0.55)
        except KeyboardInterrupt:
            self.pace = "fast"

    # Events that change state without putting anything on screen. Anything
    # else counts as having printed, which is what re-arms a gate.
    SILENT = frozenset({"Pause", "TextSpeed", "PaletteChanged", "Effect",
                        "EffectEnd", "Vocab"})

    # -- dispatch --------------------------------------------------------
    def render(self, events):
        for event in events:
            # Handlers that print directly rather than through a primitive
            # still have to arm the next gate. Marked BEFORE the handler runs,
            # so an event that gates itself - a long narration - consumes its
            # own press and the Pause after it does not fire a second time
            # with nothing in between.
            if event.kind not in self.SILENT:
                self._mark()
            handler = getattr(self, f"_e_{event.kind}", None)
            if handler:
                handler(event)

    # -- world -----------------------------------------------------------
    def _e_PaletteChanged(self, e):
        self.set_palette(e.palette)
        if e.get("notify", True):
            from frontends.terminal.effects import notify_browser
            notify_browser("palette", palette=e.palette)

    def _e_RoomEntered(self, e):
        print()
        self.rule("=")
        # A room whose text carries the reveal keeps a plain header, or the
        # spectrum arrives above the line that is supposed to introduce it.
        reveals = RAINBOW_FROM in str(e.desc)
        print(self._paint(e.name.upper()) if (self.rainbow and not reveals)
              else self._style(e.name.upper(), "bold"))
        self.rule("=")
        lines = self.wrap(e.desc, paint=True)
        exits = ", ".join(d.upper() for d in e.exits)
        print()
        print(self._c(f"Exits: {exits or 'none'}", "cyan"))
        if e.first_visit:
            self.gate_if_long(lines)

    def _e_ArtShown(self, e):
        print()
        self.art(e.art)

    def _e_Narration(self, e):
        """Marked and dimmed, so narration never reads as room description."""
        print()
        print(self._style("[** Narrator **]", "dim"))
        lines = 1
        for para in str(e.text).split("\n\n"):
            para = " ".join(para.split())
            if not para:
                continue
            block = textwrap.fill(para, self.width)
            print(self._style(block, "grey"))
            lines += block.count("\n") + 1
        self.gate_if_long(lines)

    def _e_Speech(self, e):
        if not e.text:
            return
        print()
        self.wrap(f"{e.speaker}: {e.text}", paint=True)

    def _e_Voice(self, e):
        """The thing that answers empty rooms.

        Same shape as the narrator so it reads as a speaker rather than as
        description: the label carries the narrator's dim styling, so the
        two markers sit at the same weight. The body is purple where the
        narrator's is grey — they are not the same thing and the player
        should never have to work out which one just spoke.
        """
        print()
        print(self._style("[** Voice **]", "dim"))
        said = str(e.text).strip().strip('"')
        for para in said.split("\n\n"):
            para = " ".join(para.split())
            if para:
                print(self._style(textwrap.fill(para, self.width), "purple"))

    def _e_Plain(self, e):
        if not e.text:
            return
        self.wrap(e.text)

    def _e_TextSpeed(self, e):
        self.cps = e.cps

    def _e_Effect(self, e):
        run = getattr(self.fx, e.name, None)
        if run is None:
            return
        print()
        if e.get("persist"):
            run(persist=True)
        elif e.get("seconds"):
            run(seconds=float(e.seconds))
        else:
            run()

    def _e_FloorCleared(self, e):
        from frontends.terminal.effects import notify_browser
        notify_browser("floor_cleared", floor=e.floor)
        return self._floor_cleared_body(e)

    def _e_EffectEnd(self, e):
        from frontends.terminal.effects import _notify_end, notify_browser
        if e.name in ("cold", "colour_gag"):
            # Both leave the page in a palette; put it back on the way out.
            notify_browser("palette", palette="full")
        _notify_end(e.name)

    def _e_Pause(self, e):
        self.gate()

    def _strip_marker(self, text):
        return str(text).replace(RAINBOW_FROM, "")

    def _e_Block(self, e):
        """Pre-formatted, but never allowed to overflow.

        Short lines print as written so deliberate layout survives. Anything
        wider than the screen is wrapped under its own leading indent, which
        is what used to spill off the side of a phone.
        """
        print()
        count = 0
        for line in self._strip_marker(e.text).split("\n"):
            line = line.rstrip()
            if len(line) <= self.width:
                print(line)
                count += 1
                continue
            indent = " " * (len(line) - len(line.lstrip()))
            block = textwrap.fill(line, self.width, initial_indent="",
                                  subsequent_indent=indent + "  ")
            print(block)
            count += block.count("\n") + 1
        self.gate_if_long(count)

    def _e_MemoList(self, e):
        """One line per memo, trimmed to fit. A wrapped label in a numbered
        list reads as a second entry, so the label is cut instead."""
        print()
        print(self._style(e.header, "bold"))
        print()
        for i, label in enumerate(e.entries, 1):
            prefix = f"  [{i}] "
            room = max(12, self.width - len(prefix) - 1)
            if len(label) > room:
                label = label[:room - 3].rsplit(" ", 1)[0] + "..."
            print(prefix + label)
        print()
        self.wrap(e.hint)

    def _e_Memo(self, e):
        """Left aligned, wrapped to the screen, green when newly found.

        The text carries no hand-made line breaks - paragraphs only - so it
        reflows to whatever width the terminal actually is rather than
        arriving pre-broken for somebody else's screen.
        """
        print()
        for para in e.text.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            self.wrap(para, colour="green" if e.fresh else None)
            print()

    def _e_Error(self, e):
        self.wrap(e.text, colour="red")

    # -- combat ----------------------------------------------------------
    def _e_CombatApproaching(self, e):
        print()
        self.rule("*")
        print(self._paint(e.text) if self.rainbow
              else self._style(e.text, "bold"))
        self.rule("*")
        self.gate("[enter to see what it is]")

    def _e_CombatStarted(self, e):
        names = ", ".join(e.enemies)
        header = f"COMBAT: {names}"
        if e.surprised:
            header += "   [YOU ARE SURPRISED]"
        print()
        if self.rainbow:
            print(self._paint(header))
        else:
            print(self._c(header, "red") if self.colour
                  else self._style(header, "bold"))
        self.rule("*")
        self.gate()

    def _e_DiceRolled(self, e):
        dice_str = "+".join(str(r) for r in e.rolls) if e.rolls else "-"
        line = f"({e.purpose}) {e.formula}: [{dice_str}] = {e.total}"
        if e.crit:
            line += "  CRITICAL"
        elif e.fumble:
            line += "  FUMBLE"
        wrapped = textwrap.fill(line, self.width, subsequent_indent="    ")
        print(self._style(wrapped, "dim") if not (e.crit or e.fumble)
              else self._c(wrapped, "yellow"))

    def _e_RoundStarted(self, e):
        """The whole board, every round, so the sheet is never needed mid-fight."""
        self._pause()
        print()
        print(self._style(f"-- round {e.round} --", "dim"))

        rows = []
        if e.player:
            tag = "" if not e.player["downed"] else "  DOWN"
            rows.append((e.player["name"], e.player["hp"],
                         e.player["hp_max"], f"AC {e.player['ac']}{tag}"))
        if e.companion:
            note = "" if e.companion["alive"] else "out of service"
            rows.append((e.companion["name"], e.companion["hp"],
                         e.companion["hp_max"], note))
        for foe in e.enemies:
            label = (f"[{foe['index']}] {foe['name']}" if len(e.enemies) > 1
                     else foe["name"])
            rows.append((label, foe["hp"], foe["hp_max"],
                         ", ".join(foe["statuses"])))
        if not rows:
            return

        bar_width = 10 if self.width >= 56 else 6
        # Bar plus "  nnnn/nnnn  " is fixed; the name gets whatever is left
        # after leaving room for a note, rather than eating it.
        fixed = bar_width + 2 + 11
        name_width = min(max(len(r[0]) for r in rows),
                         max(8, self.width - fixed - 8))
        for name, hp, hp_max, note in rows:
            label = name if len(name) <= name_width else name[:name_width - 1] + "."
            line = (f"{label.ljust(name_width)}  {self.bar(hp, hp_max, bar_width)}"
                    f" {hp:>4}/{hp_max:<4}")
            if note and len(line) + 2 + len(note) <= self.width:
                print(f"{line}  {note}".rstrip())
            elif note:
                print(line.rstrip())
                print(f"{' ' * (name_width + 2)}{note}"[:self.width].rstrip())
            else:
                print(line.rstrip())
        if len(e.enemies) > 1:
            print(self._style("(attack 2 to pick a target)", "dim"))

    def _e_TurnStarted(self, e):
        if e.actor_kind != "player":
            self._pause()
        print()
        marker = ">" if e.actor_kind == "player" else "*"
        print(self._style(f"{marker} {e.actor}", "bold"))

    def _e_Defeated(self, e):
        print()
        bar = "=" * min(self.width, 58)
        print(self._style(bar, "bold"))
        headline = f"{e.name.upper()} IS FINISHED"
        if self.rainbow:
            print(self._paint(headline))
        else:
            print(self._c(headline, "yellow") if self.colour
                  else self._style(headline, "bold"))
        print(self._style(bar, "bold"))
        lines = self.wrap(e.text, paint=True)
        print(self._style(bar, "bold"))
        self.gate_if_long(lines)

    def _e_AttackResolved(self, e):
        if e.hit:
            tag = "CRIT" if e.crit else "HIT"
            self.wrap(f"{e.actor} -> {e.target}: {tag} for {e.dmg}.")
        else:
            self.wrap(f"{e.actor} -> {e.target}: MISS. {e.note}")

    def _e_StatusChanged(self, e):
        verb = "gains" if e.applied else "loses"
        suffix = f" ({e.rounds} rounds)" if e.rounds else ""
        self.wrap(f"{e.actor} {verb} {e.status}{suffix}.")

    def _e_CombatEnded(self, e):
        print()
        if e.outcome == "won":
            print(self._style("Combat over.", "bold"))
        elif e.outcome == "fled":
            print(self._style("You are out.", "bold"))

    # -- progress --------------------------------------------------------
    def _e_ItemFound(self, e):
        print()
        self.wrap(f"+ {e.name}")
        if e.note:
            self.wrap(e.note, indent="  ")

    def _e_CurrencyChanged(self, e):
        sign = "+" if e.amount > 0 else ""
        self.wrap(f"{sign}{e.amount} {e.unit}.  (you have {e.total})")

    def _e_LevelUp(self, e):
        print()
        self.rule("=")
        print(self._style(f"LEVEL {e.new_level}   (+{e.hp_gain} max HP)", "bold"))
        if e.get("note"):
            print()
            self.wrap(e.note)
        for choice in e.choices:
            print()
            print(f"New ability: {choice}")
        self.rule("=")
        self.gate()

    def _e_SafeRoomRested(self, e):
        print()
        self.wrap(f"Rested. {self.bar(e.hp, e.hp_max)} {e.hp}/{e.hp_max} HP")

    def _e_MinigamePrompt(self, e):
        print()
        self.rule("-")
        self.wrap(e.mg_state)
        self.rule("-")

    def _floor_cleared_body(self, e):
        print()
        left = 13 - e.floor
        lines = [f"{e.name.upper()} {e.get('verb', 'DISPUTED')}"]
        if left > 0:
            lines.append(f"{e.floor} down. {left} to go.")
        else:
            lines.append("that was the last one.")
        inner = max(len(line) for line in lines) + 4
        inner = min(inner, self.width - 2)
        bar = "+" + "=" * inner + "+"
        print(self._style(bar, "bold"))
        for line in lines:
            print(self._style("|" + line.center(inner)[:inner] + "|", "bold"))
        print(self._style(bar, "bold"))
        self.gate()

    def _e_ContinueSpent(self, e):
        print()
        print(self._style("REPRIEVE CLAIMED", "bold"))

    def _e_RunEnded(self, e):
        print()
        self.rule("=")
        label = {"died": "RUN OVER", "cleared": "ARBITRATION COMPLETE",
                 "floor_complete": "FLOOR COMPLETE",
                 "withdrawn": "WITHDRAWN - CONTRACT VOID",
                 "signed": "SIGNED",
                 "fought": "ARBITRATION COMPLETE",
                 "free_leave": "OUT",
                 "free_take": "OUT, AND NOT ALONE",
                 "upstairs": "STILL IN HERE, AND NOT FINISHED"}.get(
                     e.reason, "RUN ENDED")
        print(self._style(label, "bold"))
        self.rule("=")
        stats = e.stats
        for label_text, key in [
                ("Rooms entered", "rooms_entered"), ("Kills", "kills"),
                ("Chests opened", "chests_opened"),
                ("Damage dealt", "damage_dealt"), ("Damage taken", "damage_taken"),
                ("Natural 20s", "nat20s"), ("Natural 1s", "nat1s"),
                ("Pounces survived", "pounces_survived"),
                ("Minigames won", "minigames_won")]:
            print(f"{label_text:<20} {stats[key]}")

    # -- panels ----------------------------------------------------------
    def _e_Portrait(self, e):
        for entry in e.entries:
            print()
            self.rule("-")
            print(self._style(f"{entry['label']}: {entry['name']}", "bold"))
            self.rule("-")
            self.art(entry["art"])
            if entry["note"]:
                print()
                self.wrap(entry["note"])

    def _e_Sheet(self, e):
        p = e.payload
        print()
        self.rule("=")
        print(self._style(f"{p['name']}  -  {p['cls']}  -  Level {p['level']}",
                          "bold"))
        self.rule("=")
        print(f"{p['currency']} {p['currency_name']}")
        xp_line = f"XP  {p['xp']}"
        if p["xp_next"]:
            xp_line += f" / {p['xp_next']}"
        print(xp_line)
        print(f"HP  {self.bar(p['hp'], p['hp_max'])} {p['hp']}/{p['hp_max']}"
              f"    AC {p['ac']}")
        print("  ".join(f"{k.upper()} {v}" for k, v in p["stats"].items()))
        if p["statuses"]:
            print("Status: " + ", ".join(f"{k} ({v})"
                                         for k, v in p["statuses"].items()))
        print()
        print("Equipped:")
        for slot, name in p["equipped"].items():
            print(f"  {slot:<8} {name}")
        print()
        print("Abilities:")
        for ab in p["abilities"]:
            print(f"  {ab['name']}   {ab['uses_left']}/{ab['uses_max']} uses")
            self.wrap(ab["desc"], indent="    ")
        comp = p["companion"]
        print()
        status = "" if comp["alive"] else "  [DOWN]"
        print(f"Companion: {comp['name']}  "
              f"{self.bar(comp['hp'], comp['hp_max'], 10)} "
              f"{comp['hp']}/{comp['hp_max']}{status}")
        self.wrap(comp["passive"], indent="  ")
        if p["stalkers"]:
            print()
            print("Following you:")
            for stalk in p["stalkers"]:
                print(f"  {stalk['name']}  -  {stalk['distance']} room(s) behind")
        if p["continue_available"]:
            print()
            print("One reprieve available.")
        if p.get("keepsake_count"):
            print()
            print(f"Record: {p['keepsake_count']} kept. Type RECORD to read them.")
        self.rule("=")

    def _e_Record(self, e):
        """Keepsakes and notes, kept off the sheet because they crowded it."""
        print()
        self.rule("=")
        print(self._style("THE RECORD", "bold"))
        self.rule("=")
        print(f"{e.currency} {e.currency_name}")
        if not e.entries and not e.notes:
            print()
            print("Nothing kept yet.")
            self.rule("=")
            return
        for entry in e.entries:
            print()
            print(self._style(_title_case(entry["name"]), "bold"))
            if entry["desc"]:
                self.wrap(entry["desc"], indent="  ")
        for note in e.notes:
            print()
            self.wrap(note)
        self.rule("=")

    def _e_Inventory(self, e):
        print()
        self.rule("-")
        print(self._style(f"INVENTORY  ({len(e.items)}/{e.cap})", "bold"))
        self.rule("-")
        if not e.items:
            print("Empty.")
            return
        for item in e.items:
            tags = []
            if item.get("spare"):
                # A second copy of what you are already wearing or wielding.
                tags.append("spare")
            elif item["equippable"]:
                tags.append("equippable")
            if item["usable"]:
                tags.append("usable")
            qty = f" x{item['qty']}" if item["qty"] > 1 else ""
            tag = f"  [{', '.join(tags)}]" if tags else ""
            print(f"{_title_case(item['name'])}{qty}{tag}")
            self.wrap(item["desc"], indent="    ")

    def _e_Shop(self, e):
        p = e.payload
        print()
        self.rule("=")
        heading = "VENDING" if p.get("machine") else "STOCK"
        print(self._style(f"{heading}    -    you have {p['currency']} "
                          f"{p['currency_name']}", "bold"))
        print(self._style(f"carrying {p['slots_used']}/{p['slots_total']}", "dim"))
        self.rule("=")
        for row in p["stock"]:
            if row["sold"]:
                print(f"[{row['n']}] {_title_case(row['name'])}  -  SOLD")
                continue
            afford = "" if row["affordable"] else "   (cannot afford)"
            print(f"[{row['n']}] {_title_case(row['name'])}   "
                  f"{row['price']}{afford}")
            self.wrap(row["desc"], indent="    ")
        if p.get("upgrade"):
            up = p["upgrade"]
            afford = "" if up["affordable"] else "   (cannot afford)"
            print()
            print(f"[bag] {up['name']}   {up['price']}{afford}")
            self.wrap(up["desc"], indent="    ")
        if p["sellable"]:
            print()
            print(self._style("HE WILL TAKE", "bold"))
            for row in p["sellable"]:
                qty = f" x{row['qty']}" if row["qty"] > 1 else ""
                print(f"  {row['name']}{qty}   {row['value']} each")
        print()
        if p.get("haggle"):
            # Above the hint, dim, so it reads as an aside about the prices
            # you have just been shown rather than as another command.
            self.wrap(self._style(p["haggle"], "dim"))
        self.wrap(p["hint"])
        self.rule("=")

    def _e_MapShown(self, e):
        print()
        self.rule("-")
        tier_name = {1: "The Audit Trail", 2: "The Extended Log",
                     3: "Full Disclosure"}.get(e.tier, "Map")
        # Floor name first, map tier in brackets after it. The old
        # "FLOOR 11  -  Full Disclosure" read as though Full Disclosure were
        # the name of the floor.
        title = e.floor_name or f"Floor {e.floor}"
        heading = f"{title}  ({tier_name})"
        if len(heading) > self.width:
            heading = f"{title}\n({tier_name})"
        print(self._style(heading, "bold"))
        self.rule("-")
        for line in self._draw_map(e.cells):
            print(line)
        print()
        print(e.legend)

    def _draw_map(self, cells):
        if not cells:
            return ["(nothing recorded)"]
        min_x = min(c["x"] for c in cells)
        min_y = min(c["y"] for c in cells)
        max_x = max(c["x"] for c in cells)
        max_y = max(c["y"] for c in cells)
        cols = (max_x - min_x + 1) * 6 + 2
        rows = (max_y - min_y + 1) * 2 + 1
        grid = [[" "] * cols for _ in range(rows)]

        def put(row, col, text):
            for i, ch in enumerate(text):
                if 0 <= row < rows and 0 <= col + i < cols:
                    grid[row][col + i] = ch

        for cell in cells:
            col = (cell["x"] - min_x) * 6
            row = (cell["y"] - min_y) * 2
            put(row, col, f"[{cell['label']}]")
            for direction, kind in cell["exits"].items():
                walked = kind == "walked"
                if direction == "east":
                    put(row, col + 3, "---" if walked else "-  ")
                elif direction == "south":
                    put(row + 1, col + 1, "|" if walked else ":")
        out = ["".join(line).rstrip() for line in grid]
        while out and not out[-1]:
            out.pop()
        return out
