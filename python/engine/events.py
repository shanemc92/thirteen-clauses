"""Events are the entire API between engine and renderer.

The engine emits these. The renderer decides how (or whether) to show them.
The renderer must never inspect GameState to decide what to say.
"""


class Event:
    __slots__ = ("kind", "data")

    def __init__(self, kind: str, **data):
        self.kind = kind
        self.data = data

    def __getattr__(self, name):
        try:
            return self.data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get(self, name, default=None):
        return self.data.get(name, default)

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.data.items())
        return f"{self.kind}({args})"


# Convenience constructors. Keep this list small and stable.
def room_entered(room_id, name, desc, exits, first_visit, kind="normal"):
    return Event("RoomEntered", room_id=room_id, name=name, desc=desc,
                 exits=exits, first_visit=first_visit, room_kind=kind)


def narration(text, voice="default"):
    return Event("Narration", text=text, voice=voice)


def speech(speaker, text):
    return Event("Speech", speaker=speaker, text=text)


def voice(text):
    """The thing that answers empty rooms. Rendered apart from real speech."""
    return Event("Voice", text=text)


def combat_approaching(text):
    """Emitted before any art or name, so the reveal lands after a beat."""
    return Event("CombatApproaching", text=text)


def art_shown(key, art):
    return Event("ArtShown", key=key, art=art)


def plain(text):
    return Event("Plain", text=text)


def effect(name, text="", persist=False, seconds=0):
    """Ask the renderer for an animation. Renderers may ignore it.

    `persist` means it runs until an EffectEnd, rather than for a duration.
    """
    return Event("Effect", name=name, text=text, persist=persist,
                 seconds=seconds)


def effect_end(name=""):
    """End a running effect. Empty name ends all timed effects."""
    return Event("EffectEnd", name=name)


def text_speed(cps):
    """Characters per second for prose, or 0 for instant."""
    return Event("TextSpeed", cps=cps)


def pause():
    """A hard stop for the renderer. Used where a wall of text would scroll."""
    return Event("Pause")


def block(text):
    """Pre-formatted text. The renderer must not re-wrap this."""
    return Event("Block", text=text)


def memo(text, fresh=False):
    """A note off a wall. Wrapped like prose, not laid out like a block.

    `fresh` marks the first reading, which the renderer greens: the memo is
    the only thing on screen at that moment worth looking at. Re-reads from
    the MEMOS list come through plain.
    """
    return Event("Memo", text=text, fresh=fresh)


def memo_list(entries, header, hint):
    """The MEMOS index. Labels arrive whole; the renderer trims them.

    Truncating here would need to know the screen width, which the engine
    deliberately does not.
    """
    return Event("MemoList", entries=entries, header=header, hint=hint)


def error(text):
    return Event("Error", text=text)


def dice_rolled(formula, rolls, modifier, total, purpose, crit=False, fumble=False):
    return Event("DiceRolled", formula=formula, rolls=rolls, modifier=modifier,
                 total=total, purpose=purpose, crit=crit, fumble=fumble)


def turn_started(actor, actor_kind, round_n):
    return Event("TurnStarted", actor=actor, actor_kind=actor_kind, round=round_n)


def round_started(round_n, enemies=None, player=None, companion=None):
    return Event("RoundStarted", round=round_n, enemies=enemies or [],
                 player=player, companion=companion)


def defeated(name, text):
    return Event("Defeated", name=name, text=text)


def combat_started(enemies, surprised=False):
    return Event("CombatStarted", enemies=enemies, surprised=surprised)


def attack_resolved(actor, target, hit, crit, dmg, note=""):
    return Event("AttackResolved", actor=actor, target=target, hit=hit,
                 crit=crit, dmg=dmg, note=note)


def status_changed(actor, status, applied, rounds=0):
    return Event("StatusChanged", actor=actor, status=status,
                 applied=applied, rounds=rounds)


def combat_ended(outcome, xp=0, loot=None):
    return Event("CombatEnded", outcome=outcome, xp=xp, loot=loot or [])


def level_up(new_level, hp_gain, choices, note=""):
    return Event("LevelUp", new_level=new_level, hp_gain=hp_gain,
                 choices=choices, note=note)


def item_found(item_id, name, note=""):
    return Event("ItemFound", item_id=item_id, name=name, note=note)


def stalker_closer(monster, distance, name):
    return Event("StalkerCloser", monster=monster, distance=distance, name=name)


def stalker_lost(monster, name):
    return Event("StalkerLost", monster=monster, name=name)


def pounced(monster, name):
    return Event("Pounced", monster=monster, name=name)


def map_shown(cells, legend, tier, floor, floor_name=""):
    return Event("MapShown", cells=cells, legend=legend, tier=tier,
                 floor=floor, floor_name=floor_name)


def palette_changed(palette, notify=True):
    """`notify` False keeps the change terminal-only, with no browser overlay."""
    return Event("PaletteChanged", palette=palette, notify=notify)


def safe_room_rested(hp_restored, hp, hp_max):
    return Event("SafeRoomRested", hp_restored=hp_restored, hp=hp, hp_max=hp_max)


def minigame_prompt(mg_state):
    return Event("MinigamePrompt", mg_state=mg_state)


def portrait(entries):
    """entries: [{"label", "name", "art", "note"}]"""
    return Event("Portrait", entries=entries)


def shop(payload):
    return Event("Shop", payload=payload)


def currency_changed(amount, total, reason="", unit="units"):
    return Event("CurrencyChanged", amount=amount, total=total,
                 reason=reason, unit=unit)


def record(entries, notes, currency, currency_name):
    return Event("Record", entries=entries, notes=notes, currency=currency,
                 currency_name=currency_name)


def sheet(payload):
    return Event("Sheet", payload=payload)


def inventory(items, cap):
    return Event("Inventory", items=items, cap=cap)


def continue_spent(floor):
    return Event("ContinueSpent", floor=floor)


def floor_cleared(floor, name, verb="DISPUTED"):
    """The banner at the end of a floor.

    `verb` because Clause 13 can end five ways and only one of them is a
    dispute: signing is not disputing, and walking out is not either.
    """
    return Event("FloorCleared", floor=floor, name=name, verb=verb)


def run_ended(reason, stats):
    return Event("RunEnded", reason=reason, stats=stats)
