"""Terminal frontend. Owns all I/O. The engine owns everything else."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from engine import actions, minigames, saves, step as step_mod  # noqa: E402
from engine.content import load_from_disk                    # noqa: E402
from engine.state import (MODE_CHOICE, MODE_COMBAT, MODE_DEAD,  # noqa: E402
                          MODE_MINIGAME, MODE_SHOP, MODE_WON)
from frontends.terminal.inputline import Input               # noqa: E402
from frontends.terminal.render import Renderer               # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTENT_DIR = os.path.join(ROOT, "content")
SAVE_DIR = os.path.join(ROOT, "saves")
HISTORY = os.path.join(SAVE_DIR, ".history")

HELP_SECTIONS = [
    ("MOVEMENT", [
        ("n / s / e / w", "move"),
        ("look", "describe the room again"),
        ("map", "your path, if you have something to read it with"),
    ]),
    ("ACTIONS", [
        ("take", "open or pick up what is here"),
        ("read", "read what is written here"),
        ("talk", "speak to whoever is here"),
        ("rest", "recover, safe rooms only"),
        ("use <item>", "use an item"),
        ("equip <item>", "equip a weapon or armour"),
        ("unequip <slot>", "put gear back in the bag"),
        ("drop <item>", "put something down for good"),
    ]),
    ("COMBAT", [
        ("attack", "attack the nearest enemy"),
        ("attack 2", "pick a target when several share a name"),
        ("ability", "list what you can do and how often"),
        ("ability <name>", "use one; a partial name is enough"),
        ("<ability name>", "shortcut, if you type the whole name"),
        ("flee", "try to escape; survivors may follow you"),
        ("combat", "how the dice and turn order work"),
    ]),
    ("TRADING", [
        ("buy <n>", "buy stock item n"),
        ("buy bag", "buy more carrying room"),
        ("sell <item>", "he takes it at a third"),
        ("leave", "stop shopping"),
    ]),
    ("CHARACTER", [
        ("sheet", "stats, gear and abilities"),
        ("char", "your portrait and your companion's"),
        ("inv", "inventory"),
        ("record", "keepsakes, proofs and things bosses left behind"),
        ("memos", "everything you have read off the walls"),
        ("memos <n>", "read one of them again"),
    ]),
    ("ODDMENTS", [
        ("withdraw", "state your position, out loud"),
        ("sing", "only worth it where nothing is hunting you"),
        ("photo", "look at the photograph in a safe room"),
    ]),
    ("META", [
        ("pace", "which pacing you are on"),
        ("pace fast|slow|manual", "combat pacing"),
        ("width <n>", "force screen width"),
        ("save [name]", "write a save file"),
        ("load [name]", "load one; no name loads the newest"),
        ("effects on|off", "toggle text/overlay effects"),
        ("version", "which build this is"),
        ("help", "this"),
        ("quit", "leave"),
    ]),
]


def show_help(renderer):
    """Two columns where they fit, stacked where they do not."""
    width = renderer.width
    labels = [cmd for _, rows in HELP_SECTIONS for cmd, _ in rows]
    gutter = max(len(c) for c in labels) + 2
    stacked = gutter + 24 > width
    for title, rows in HELP_SECTIONS:
        print()
        print(renderer._style(title, "bold"))
        for cmd, desc in rows:
            if stacked:
                print(f"  {cmd}")
                renderer.wrap(desc, indent="      ")
            else:
                renderer.wrap(f"  {cmd.ljust(gutter)}{desc}",
                              hanging=" " * (gutter + 2))


WORD_ACTIONS = {
    "n": "n", "north": "n", "s": "s", "south": "s",
    "e": "e", "east": "e", "w": "w", "west": "w",
}


def parse(text, state, content):
    """Text -> Action. Returns None for frontend-handled commands."""
    parts = text.strip().split()
    if not parts:
        return None
    # A minigame owns the whole command space while it is running. This has to
    # come before the verb table: eleven of the twenty-six letters are verb
    # aliases ("a" attacks, "e"/"n"/"s"/"w" move, "i" is inventory, "y" answers
    # a prompt), so hangman could not be played at all — the guess became a
    # command, the engine rejected it as "not now", and the game looked like it
    # was refusing input. Nothing else is reachable from here on purpose:
    # HELP, QUIT, VERSION and WIDTH are handled by the frontend before parse()
    # and still work.
    # Any named-choice prompt owns the command space while it is up, the same
    # way a minigame does. The verb table used to get there first, so only
    # YES and NO ever reached a prompt: the Signatory's ASK/SIGN/REFUSE and
    # the Narrator's LEAVE/TAKE all parsed as something else or as nothing,
    # and WITHDRAW became the global withdrawal count. Keyed on there being
    # options rather than on the kind, so a new prompt cannot be added
    # without the parser knowing about it.
    if state.mode == MODE_CHOICE and (state.pending or {}).get("options"):
        return actions.choose(text.strip().lower())

    if state.mode == MODE_MINIGAME:
        # Bids look like "4 3". Passing only the first word meant every bid
        # was rejected as malformed and the game could not be finished.
        return actions.minigame(text.strip())

    verb = parts[0].lower()
    rest = " ".join(parts[1:])

    # Ability names win over the verb table, but only on an exact match.
    # "hit and run" used to be read as attack("and run") and "no case to
    # answer" as a yes/no answer, so neither ability could ever be used.
    # Exact-only means a bare "hit" still attacks and a bare "no" still
    # answers a prompt.
    if state.mode == MODE_COMBAT:
        aid = _match_ability(text, state, content, exact=True)
        if aid:
            return actions.ability(aid)

    if verb in ("memos", "memo", "notes"):
        return actions.memos(rest)
    if verb in ("ability", "ab", "cast"):
        return _ability_action(rest, state, content)
    if verb in WORD_ACTIONS:
        return actions.move(WORD_ACTIONS[verb])
    if verb == "go" and rest:
        short = WORD_ACTIONS.get(rest.lower().split()[0])
        return actions.move(short) if short else actions.move(rest)
    if verb in ("look", "l"):
        return actions.look()
    if verb in ("map", "m"):
        return actions.show_map()
    if verb in ("sheet", "stats", "c"):
        return actions.sheet()
    if verb in ("char", "portrait", "me"):
        return actions.portrait()
    if verb in ("record", "keepsakes", "trophies"):
        return actions.record()
    if verb in ("inv", "inventory", "i"):
        return actions.inventory()
    if verb in ("read", "inspect"):
        return actions.read(rest)
    if verb in ("take", "open", "get", "loot"):
        return actions.take(rest)
    if verb == "talk":
        return actions.talk()
    if verb in ("buy", "b"):
        return actions.buy(rest)
    if verb == "sell":
        return actions.sell(rest)
    if verb in ("leave", "bye", "done"):
        return actions.leave_shop()
    if verb == "rest":
        return actions.rest()
    if verb == "use":
        return actions.use(_match_item(rest, state, content))
    if verb == "equip":
        return actions.equip(_match_item(rest, state, content))
    if verb in ("unequip", "remove", "stow"):
        return actions.unequip(rest)
    if verb == "drop":
        return actions.drop(_match_item(rest, state, content))
    if verb in ("attack", "a", "hit"):
        return actions.attack(rest)
    if verb == "flee":
        return actions.flee()
    if verb == "wait":
        return actions.wait()
    if verb in ("withdraw", "cancel", "unsubscribe"):
        return actions.withdraw()
    if verb in ("sing", "hum", "whistle"):
        return actions.sing()
    if verb in ("photo", "photograph", "beach"):
        return actions.photo()
    if verb in ("combat", "combat?", "howto", "rules"):
        return actions.explain("combat")
    if verb == "pace":
        return actions.setting("pace", parts[1].lower() if len(parts) > 1 else "")
    if verb == "effects":
        return actions.setting("effects", parts[1].lower() if len(parts) > 1 else "")
    if verb in ("yes", "y", "no", "n0"):
        return actions.Action("Choose", "yes" if verb.startswith("y") else "no")

    if state.mode == MODE_COMBAT:
        aid = _match_ability(text, state, content)
        if aid:
            return actions.ability(aid)
        aid = _match_ability(verb, state, content)
        if aid:
            return actions.ability(aid, rest)
    return None


def _match_item(text, state, content):
    text = text.lower().strip()
    if not text:
        return ""
    for entry in state.inventory:
        item = content.item(entry["id"])
        name = content.t(item["name_key"]).lower()
        if text == entry["id"] or text in name or text in item.get("aliases", []):
            return entry["id"]
    return text


def _ability_action(rest, state, content):
    """`ability <name>` — the explicit route, and the one that always works.

    Bare ability names still work, but only on an exact match, because they
    have to share the command space with the verb table: "hit and run" was
    read as an attack and "no case to answer" as a yes/no answer. Typing
    ABILITY first takes the ability out of that contest, so a partial is
    enough - "ability hit" or "ability run".
    """
    known = [(aid, content.t(content.ability(aid)["name_key"]))
             for aid in state.player.abilities]
    want = (rest or "").lower().strip()
    if not want:
        return actions.abilities()
    hits = [(aid, name) for aid, name in known
            if want == aid or want == name.lower()]
    if not hits:
        hits = [(aid, name) for aid, name in known
                if want in name.lower() or want in aid.replace("_", " ")]
    if len(hits) == 1:
        return actions.ability(hits[0][0])
    # Nothing, or more than one thing: show the list rather than guessing.
    return actions.abilities(want)


def _match_ability(text, state, content, exact=False):
    """Whole name or id first, then a single word out of a name.

    Two passes, not one: with a single pass an earlier ability's loose word
    match beat a later one's exact name, so a Skirmisher who knew both Cut
    and Run and Hit and Run could not reliably name either.
    """
    text = text.lower().strip()
    if not text:
        return None
    known = [(aid, content.t(content.ability(aid)["name_key"]).lower())
             for aid in state.player.abilities]
    for aid, name in known:
        if text == aid or text == name:
            return aid
    if exact:
        return None
    for aid, name in known:
        if text in name.split():
            return aid
    return None


# ---------------------------------------------------------------- creation
def _pick(renderer, prompt, options, describe, summary=None):
    """describe(spec) -> (art_text, description_text).

    Art is printed flush left via renderer.art(); description is printed
    flush left and wrapped to the actual terminal width via renderer.wrap().
    Neither gets a hand-picked indent, so both auto-flow the same way the
    rest of the game does.
    """
    first = True
    while True:
        print()
        renderer.rule("=")
        print(prompt)
        renderer.rule("=")
        for i, (key, spec) in enumerate(options, 1):
            art, desc = describe(spec)
            print()
            print(renderer._style(f"[{i}] {key.upper()}", "bold"))
            print()
            renderer.art(art)
            print()
            renderer.wrap(desc)
            # One at a time. Four sprites plus four paragraphs in a single
            # dump is unreadable on a phone.
            if first and i < len(options):
                renderer.gate("[enter for the next one]")
        first = False
        if summary:
            print()
            renderer.rule("-")
            print(renderer._style("AT A GLANCE", "bold"))
            renderer.rule("-")
            for i, (key, spec) in enumerate(options, 1):
                renderer.wrap(f"[{i}] {key.upper()}   {summary(spec)}",
                              hanging="    ")
        print()
        try:
            raw = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("\n  Arbitration suspended.\n")
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        for key, _ in options:
            if raw == key.lower():
                return key
        print("Pick a number.")


def _slug(spec, content):
    """The class id for a spec, so its style text can be looked up."""
    for cid, other in content.classes.items():
        if other is spec:
            return cid
    return ""


def _stat_line(content, spec):
    """The numbers that actually differ between classes, in one line.

    AC is shown as the total you start with, not the class bonus on its own,
    which read like a total and was not one.
    """
    stats = spec["stats"]
    dex_mod = (stats["dex"] - 10) // 2
    armour = spec.get("starting_equipment", {}).get("armour")
    armour_ac = content.item(armour).get("ac", 0) if armour else 0
    ac = 10 + dex_mod + spec.get("ac_bonus", 0) + armour_ac
    hp = spec["hit_die_max"] + (stats["con"] - 10) // 2 + spec.get("hp_bonus", 0)
    return (f"HP {hp}   AC {ac}   "
            + "  ".join(f"{k.upper()} {v}" for k, v in stats.items()))


def _briefing(renderer, content):
    """The onboarding pack, in the building's own voice, before you choose.

    Sections are gated so each one can be read rather than scrolled past.
    """
    sections = [
        (None, "help.briefing.open"),
        ("WHAT YOU ARE", "help.briefing.stats"),
        ("WHAT KEEPS YOU ALIVE", "help.briefing.defence"),
        ("WHAT YOU CAN DO", "help.briefing.abilities"),
        ("WHO COMES WITH YOU", "help.briefing.companion"),
        (None, "help.briefing.close"),
    ]
    for title, key in sections:
        print()
        if title:
            renderer.rule("-")
            print(renderer._style(title, "bold"))
            renderer.rule("-")
        renderer.wrap(content.t(key))
        renderer.gate()


def create_character(renderer, content):
    print()
    renderer.wrap(content.t("floors.01.clause"))
    print()
    try:
        name = input("Your name: ").strip() or "Nobody"
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\n  Arbitration suspended.\n")

    print()
    renderer.rule("=")
    print(renderer._style("BEFORE YOU BEGIN", "bold"))
    renderer.rule("=")
    _briefing(renderer, content)

    class_options = list(content.classes.items())
    class_id = _pick(
        renderer, "CHOOSE YOUR CLASS", class_options,
        lambda spec: (content.get_art(spec["art"]),
                      f'PLAYSTYLE: {content.t(spec["verb_key"]).upper()}\n\n'
                      f'{content.t("classes." + _slug(spec, content) + ".style")}'
                      f'\n\n{content.t(spec["desc_key"])}'
                      f'\n\n{_stat_line(content, spec)}'),
        summary=lambda spec: f'Playstyle: {content.t(spec["verb_key"])}.  '
                             f'{_stat_line(content, spec)}')

    comp_options = list(content.companions.items())
    comp_id = _pick(
        renderer, "CHOOSE YOUR COMPANION", comp_options,
        lambda spec: (content.get_art(spec["art"]),
                      f'{content.t(spec["desc_key"])}  '
                      f'{content.t(spec["passive_key"])}'),
        summary=lambda spec: f'Passive: {content.t(spec["passive_key"])}')

    # Where they came from. The form has a box for it, so the answer is the
    # same as the answer to everything else in this building: somebody
    # arranged it, a long time ago, without asking either of you.
    print()
    renderer.wrap(content.t("companion.assigned",
                            name=content.t(content.companions[comp_id]["name_key"])))
    renderer.gate()

    return name, class_id, comp_id


BUILD = "2026-08-15a"


def build_line(content):
    """One line naming the code build and the content hash.

    Both matter and they move independently: the wrapper copies the program in
    fresh on every load, but a stale deployment or a cached folder will happily
    run last week's engine against this week's content. VERSION carries the
    same string.
    """
    from frontends.terminal import effects as fxmod
    where = "browser" if fxmod.IN_BROWSER else "terminal"
    return f"  build {BUILD}  -  content {content.version}  -  {where}"


def show_title(renderer, content):
    art = content.get_art("title")
    print()
    renderer.art(art)
    print()
    print(renderer._style(build_line(content), "dim"))


def save_menu_entries():
    """(label, path) newest first, with the timestamp, for the load list."""
    entries = []
    for path in find_saves():
        stamp = time.strftime("%d %b %H:%M", time.localtime(save_time(path)))
        entries.append((f"{os.path.basename(path)[:-7]}   ({stamp})", path))
    return entries


def start_menu(renderer, content):
    """Returns ('new', None) or ('load', path) or ('quit', None).

    Straight to a new run if there is nothing to load, so an empty saves
    directory never costs a keypress.
    """
    show_title(renderer, content)
    saves_found = save_menu_entries()
    if not saves_found:
        return "new", None

    while True:
        print()
        renderer.rule("=")
        # Continue is what Enter does, so Continue is what gets the emphasis.
        print(renderer._style(f"  [1] Continue      {saves_found[0][0]}",
                              "bold") + renderer._style("   (Enter)", "dim"))
        print("  [2] New run")
        if len(saves_found) > 1:
            print(f"  [3] Load a save   ({len(saves_found)} available)")
        print("  [Q] Quit")
        renderer.rule("=")
        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "quit", None

        if choice in ("1", "c", "continue", ""):
            return "load", saves_found[0][1]
        if choice in ("2", "n", "new"):
            return "new", None
        if choice in ("q", "quit", "exit"):
            return "quit", None
        if choice in ("3", "l", "load") and len(saves_found) > 1:
            picked = _pick_save(renderer, saves_found)
            if picked:
                return "load", picked
            continue
        print("  1, 2, 3 or Q.")


def _pick_save(renderer, saves_found):
    print()
    renderer.rule("-")
    for i, (label, _) in enumerate(saves_found, 1):
        print(f"  [{i}] {label}")
    print("  [B] Back")
    renderer.rule("-")
    raw = input("  > ").strip().lower()
    if raw.isdigit() and 1 <= int(raw) <= len(saves_found):
        return saves_found[int(raw) - 1][1]
    return None


# ---------------------------------------------------------------- saves
def do_save(state, name):
    os.makedirs(SAVE_DIR, exist_ok=True)
    name = (name or f"save-{int(time.time())}").replace(" ", "-")
    path = os.path.join(SAVE_DIR, f"{name}.13save")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(saves.encode(state))
    print(f"  Saved to {path}")


def save_time(path):
    """When a save was written, preferring the stamp inside the file.

    Falls back to mtime for saves written before stamping existed.
    """
    mtime = os.path.getmtime(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return saves.written_at(handle.read(), mtime)
    except OSError:
        return mtime


def find_saves():
    """Every .13save in the working directory and saves/, newest first."""
    found = {}
    for directory in (os.getcwd(), SAVE_DIR):
        if not os.path.isdir(directory):
            continue
        for fname in os.listdir(directory):
            if fname.endswith(".13save"):
                path = os.path.abspath(os.path.join(directory, fname))
                found[path] = save_time(path)
    # Name is a deterministic tie-break so equal timestamps never fall back
    # to whatever order the directory happened to list in.
    return sorted(found, key=lambda p: (-found[p], os.path.basename(p)))


def _read_save(path, content):
    with open(path, "r", encoding="utf-8") as handle:
        return saves.load_state(handle.read(), content)


def do_load(name, content):
    """Load by name, or with no name, the most recent save available."""
    if not name:
        candidates = find_saves()
        if not candidates:
            print("  No save files here or in saves/.")
            return None
        for path in candidates:
            try:
                state, warning = _read_save(path, content)
            except saves.SaveError as exc:
                if str(exc) == "dead":
                    print(f"  Skipping {os.path.basename(path)}: that run is over.")
                    continue
                print(f"  Skipping {os.path.basename(path)}: {exc}")
                continue
            if warning:
                print(f"  Note: {warning}")
            print(f"  Loaded {os.path.basename(path)} (most recent).")
            return state
        print("  Nothing loadable. Every save found is a finished run.")
        return None

    path = name if os.path.exists(name) else os.path.join(SAVE_DIR, f"{name}.13save")
    if not os.path.exists(path):
        print(f"  No save called {name}.")
        return None
    try:
        state, warning = _read_save(path, content)
    except saves.SaveError as exc:
        if str(exc) == "dead":
            print("  That run is over. The file is a record, not a game.")
        else:
            print(f"  {exc}")
        return None
    if warning:
        print(f"  Note: {warning}")
    print(f"  Loaded {os.path.basename(path)}.")
    return state


# ---------------------------------------------------------------- loop
def prompt_for(state):
    if state.mode == MODE_COMBAT:
        return "  [combat] > "
    if state.mode == MODE_MINIGAME:
        return "  [game] > "
    if state.mode == MODE_CHOICE:
        # Name the actual options. The prompt said [yes/no] at every choice
        # in the game, including the two that offer neither.
        options = (state.pending or {}).get("options")
        return "  [" + "/".join(options or ("yes", "no")) + "] > "
    if state.mode == MODE_SHOP:
        return "  [shop] > "
    return "  > "


def main(argv):
    content = load_from_disk(CONTENT_DIR)
    width = None
    if "--width" in argv:
        width = int(argv[argv.index("--width") + 1])
    renderer = Renderer(width=width)
    renderer.content = content
    reader = Input(history_path=HISTORY)

    state = None
    if "--load" in argv:
        pos = argv.index("--load") + 1
        target = argv[pos] if pos < len(argv) and not argv[pos].startswith("--") else ""
        state = do_load(target, content)
    elif "--new" not in argv:
        action, path = start_menu(renderer, content)
        if action == "quit":
            print("\n  Arbitration suspended. Your dispute remains open.\n")
            return
        if action == "load":
            state = do_load(path, content)
            if state is None:
                print("  Starting a new run instead.")
    if state is None:
        seed = int(time.time() * 1000) & 0xFFFFFFFF
        if "--seed" in argv:
            seed = int(argv[argv.index("--seed") + 1])
        name, class_id, comp_id = create_character(renderer, content)
        state, events = step_mod.new_game(content, seed, name, class_id, comp_id)
        renderer.set_palette(state.palette)
        renderer.pace = state.settings.get("pace", "slow")
        renderer.render(events)
        renderer.render(step_mod.resume(state, content))
    else:
        renderer.set_palette(state.palette)
        renderer.render(step_mod.resume(state, content))
        state, events = step_mod.step(state, actions.look(), content)
        renderer.render(events)

    print()
    print(reader.note)

    debug_enabled = False   # gates `fx`; see SPOILERS.md, not in `help` on purpose

    while True:
        reader.refresh(state, content,
                       [os.path.basename(p)[:-7] for p in find_saves()])
        try:
            raw = reader.ask(prompt_for(state)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue
        low = raw.lower()
        parts = low.split()

        if parts[0] in ("quit", "exit"):
            break
        if parts[0] == "help":
            if state.mode == MODE_MINIGAME:
                # The normal command list is all unavailable here, so showing
                # it would be a list of things that will not work.
                print()
                renderer.wrap(content.t("errors.in_minigame"), indent="  ")
                print()
                print(minigames.get(state.minigame["game"]).prompt(
                    state, content, state.minigame))
            else:
                show_help(renderer)
            continue
        if parts[0] in ("version", "build"):
            print()
            print(build_line(content))
            continue
        if parts[0] == "debug":
            # Undocumented on purpose: gates `fx` below. Not in `help`,
            # see SPOILERS.md for the whole story.
            if len(parts) > 1 and parts[1] in ("on", "off"):
                debug_enabled = parts[1] == "on"
                print(f"  debug {'on' if debug_enabled else 'off'}")
            else:
                print(f"  debug is {'on' if debug_enabled else 'off'}; "
                      f"'debug on' or 'debug off'")
            continue
        if parts[0] == "fx":
            # Undocumented on purpose: a debug hook, not a game command.
            # `effects on|off` is the real, documented, saved player setting;
            # this stays on the `fx` name alone so the two never collide.
            # Locked behind `debug on` first: a bad `fx` argument used to be
            # able to crash the session (see SPOILERS.md), so it is no longer
            # one stray keystroke away from anyone just poking at commands.
            if not debug_enabled:
                print("  Unknown command.")
                continue
            from frontends.terminal import effects as fxmod
            # Held effects run until an end; bursts time themselves out.
            # Giving a held name a number fires it as a burst instead, which
            # is the quicker way to eyeball one without typing `fx end`.
            held = ("cold", "sever", "session", "blank", "static", "lowhp")
            bursts = {"storm": 7.0, "party": 12.0, "amend": 1.6,
                      "ember": 8.0, "signature": 6.0, "colour_gag": 6.0}
            if len(parts) > 1:
                name = parts[1]
                arg = parts[2] if len(parts) > 2 else ""
                # Each effect needs its own payload shape; sending `seconds`
                # to a palette change is why `fx palette` did nothing.
                if name == "palette":
                    payload = {"palette": arg or "rainbow"}
                elif name == "floor_cleared":
                    payload = {"floor": int(arg) if arg.isdigit() else state.floor}
                elif name == "end":
                    ok = fxmod._notify_end(arg)
                    print(f"  ended {arg or 'all timed effects'} -> "
                          f"{'sent' if ok else 'FAILED'}")
                    continue
                elif arg in ("hold", "0") or (name in held and not arg):
                    payload = {"persist": True, "seconds": 86400.0}
                else:
                    seconds = bursts.get(name, 8.0)
                    if arg:
                        try:
                            seconds = float(arg)
                        except ValueError:
                            print(f"  '{arg}' isn't a number of seconds. "
                                  f"Try: fx {name} <seconds> | hold | 0")
                            continue
                    payload = {"seconds": seconds}
                ok = fxmod.notify_browser(name, **payload)
                print(f"  fired {name!r} {payload} -> "
                      f"{'sent' if ok else 'FAILED'}")
            print()
            print(fxmod.diagnostics())
            print()
            print("  Browser overlay only. The terminal draws its own storm")
            print("  and party; everything else here is page-side.")
            print()
            print("  held (run until ended)")
            print("    fx cold | fx sever | fx session | fx blank")
            print("    fx static | fx lowhp | fx storm hold     (or: 0)")
            print("  burst (self-ending, [secs] optional)")
            print("    fx storm [7] | fx party [12] | fx amend [1.6]")
            print("    fx ember [8] | fx signature [6] | fx floor_cleared [n]")
            print("  palette")
            print("    fx palette [rainbow|mono|full] | fx colour_gag [6]")
            print("  ending")
            print("    fx end <name>    one effect")
            print("    fx end           all timed effects, palette untouched")
            print()
            print("  A held name with a number fires as a burst: fx lowhp 4")
            continue
        if parts[0] == "width":
            if len(parts) > 1 and parts[1].isdigit():
                renderer.width = max(32, min(int(parts[1]), 120))
                print(f"  Width set to {renderer.width}.")
            else:
                print(f"  Current width {renderer.width}. Usage: width <n>")
            continue
        if parts[0] in ("save", "load") and state.mode in (
                MODE_COMBAT, MODE_MINIGAME):
            # "load bearing" is a Vanguard ability, not a request to load a
            # save file called "bearing", so the parser gets first refusal.
            # Only complain if it really was a save or load.
            action = parse(raw, state, content)
            if action is None:
                print("  Not in the middle of this. Finish the fight first.")
                continue
            state, events = step_mod.step(state, action, content)
            renderer.render(events)
            continue
        if parts[0] == "save":
            do_save(state, parts[1] if len(parts) > 1 else "")
            continue
        if parts[0] == "load":
            loaded = do_load(parts[1] if len(parts) > 1 else "", content)
            if loaded:
                state = loaded
                renderer.set_palette(state.palette)
                renderer.render(step_mod.resume(state, content))
                state, events = step_mod.step(state, actions.look(), content)
                renderer.render(events)
            continue

        action = parse(raw, state, content)
        if action is None:
            print("  Not a command. Type HELP.")
            continue

        state, events = step_mod.step(state, action, content)
        renderer.pace = state.settings.get("pace", "slow")
        renderer.render(events)

        if state.mode in (MODE_DEAD, MODE_WON):
            print()
            again = input("  New run? (y/n) > ").strip().lower()
            if again.startswith("y"):
                seed = int(time.time() * 1000) & 0xFFFFFFFF
                show_title(renderer, content)
                name, class_id, comp_id = create_character(renderer, content)
                state, events = step_mod.new_game(content, seed, name,
                                                  class_id, comp_id)
                renderer.set_palette(state.palette)
                renderer.render(events)
                renderer.render(step_mod.resume(state, content))
            else:
                break

    print("\n  Arbitration suspended. Your dispute remains open.\n")


if __name__ == "__main__":
    main(sys.argv)
