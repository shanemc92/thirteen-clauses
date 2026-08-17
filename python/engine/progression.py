"""XP, levels, ability grants. Twenty-five levels across thirteen floors."""

from . import events as ev
from . import dice

# Fitted to the XP a thorough run actually earns. Instrumenting twelve full
# playthroughs put the median at ~70,600 across thirteen floors, so Level 25
# sits at 70,000: a completionist tops out on the last floor, a brisk run
# finishes around 21-22. Gaps grow monotonically, roughly two levels a floor.
#
# The opening levels used to be cheap on purpose, so Floor 1 still handed
# something out. That overshot. Floor 1 pays a median of ~800 XP across forty
# bot runs, which sat right on the old Level 4 threshold, so the tutorial
# floor was worth three levels and left the player far above its own
# monsters. Early gaps now start at 200 and rise: a median Floor 1 clear
# finishes at Level 3, a thorough one at 4. Level 10 up is untouched, so this
# redistributes the early game rather than slowing the whole run.
#
# Index i is the threshold for level i+1, so XP_TABLE[24] is what Level 25
# costs and len(XP_TABLE) is the level ceiling.
XP_TABLE = [0, 200, 600, 1200, 2000, 3000, 4200, 5600, 7200, 9100,
            11300, 13900, 16600, 19700, 23000, 26500, 30300, 34400, 38700,
            43300, 48100, 53200, 58600, 64100, 70000]

# Hard ceiling on hit points. The curve below approaches it at the level
# ceiling rather than blowing past it mid-run, so the last few levels still
# pay out instead of being silently discarded.
HP_CAP = 350


def xp_for_level(level: int) -> int:
    idx = min(level, len(XP_TABLE)) - 1
    return XP_TABLE[idx]


def next_threshold(level: int):
    if level >= len(XP_TABLE):
        return None
    return XP_TABLE[level]


def award_xp(state, content, rng, amount, out):
    state.player.xp += amount
    out.append(ev.plain(content.t("progress.xp_gain", amount=amount,
                                  total=state.player.xp)))
    while True:
        threshold = next_threshold(state.player.level)
        if threshold is None or state.player.xp < threshold:
            break
        _level_up(state, content, rng, out)


def _level_up(state, content, rng, out):
    player = state.player
    player.level += 1
    cls = content.classes[player.cls]

    _rolls, gain = dice.roll(cls["hit_die"], rng)
    gain = max(1, gain + player.mod("con") + 3)
    # Deep-floor scaling. Later levels add more on top of the hit die,
    # because monster damage grows per floor and a d10 does not. Stretched
    # from every three levels to every five for the 25-level table: the old
    # cadence was fitted to a 20-level run and, spread over five more levels,
    # put the player ~20% above the HP the deep floors were tuned against.
    # This cadence holds Floors 5-10 within a couple of percent of that
    # tuning; the surplus from Floor 11 on is what HP_CAP is there to bound.
    #
    # Spacing measured against the cap: on every-five, a Vanguard reached 350
    # at Level 21 and the last four levels paid no health at all. On every-
    # five-from-seven the four classes land at 95-119% of the cap at Level 25,
    # so the ceiling bites at the ceiling and not before.
    for tier in (7, 12, 17, 22):
        if player.level >= tier:
            gain += 3
    # Never award more than the cap allows, so the reported gain is the gain
    # the player actually got.
    gain = max(0, min(gain, HP_CAP - player.hp_max))
    player.hp_max += gain
    player.hp = player.hp_max

    if player.level % 2 == 0:
        stat = cls["primary"]
        player.stats[stat] += 1

    comp = state.companion
    if comp.cid:
        comp_gain = content.companions[comp.cid].get("level_hp", 3)
        comp.hp_max += comp_gain
        if comp.alive:
            comp.hp = min(comp.hp_max, comp.hp + comp_gain)

    grants = [a for a in cls["abilities"]
              if a["level"] <= player.level and a["id"] not in player.abilities]
    choices = []
    for grant in grants:
        player.abilities.append(grant["id"])
        spec = content.ability(grant["id"])
        player.cooldowns[grant["id"]] = spec.get("uses", 1)
        choices.append(content.t(spec["name_key"]))

    out.append(ev.level_up(player.level, gain, choices,
                           note=content.t(f"standing.{player.level}", rng)))
    art = content.get_art("levelup")
    if art:
        out.append(ev.art_shown("levelup", art))

    _press_the_card(state, content, out)


CARD_PRESSES_AT = 6


def _press_the_card(state, content, out):
    """A business card kept to Level 6 stops being your old job.

    The Advocate opens with it and it does nothing, which made it the one
    starting item that was purely vendor trash. Keeping it is now the point:
    carry it to Level 6 without selling it and it becomes a weapon, scaling
    on CHA like everything else the class swings.

    Slightly ahead of the tier a Level 6 player is otherwise holding — 2d6+3
    against the actuary's pen at 2d6+2 — and well short of the signature pen
    on Floor 6, so it is a reward for patience rather than a reason to stop
    looking. Anyone can do it; the Advocate just starts holding one.
    """
    if state.player.level < CARD_PRESSES_AT:
        return
    if not any(e["id"] == "business_card" for e in state.inventory):
        return
    state.remove_item("business_card")
    # Straight into the bag rather than equipped: swapping a player's weapon
    # out from under them mid-run is not a gift.
    state.add_item("pressed_card")
    out.append(ev.plain(content.t("progress.card_pressed")))


def restore_ability_uses(state, content):
    for aid in state.player.abilities:
        spec = content.ability(aid)
        state.player.cooldowns[aid] = spec.get("uses", 1)
    state.companion.cooldowns = {}


def carl_bonus(state, content, out):
    """Say so. A hidden bonus nobody notices is not a bonus."""
    from . import events as ev
    if state.flags.get("carl"):
        out.append(ev.block(content.t("secret.carl")))
    return out


def build_player(state, content, rng, name, class_id, companion_id):
    from .state import Player, Companion
    cls = content.classes[class_id]
    player = Player(name=name, cls=class_id, level=1)
    player.stats = dict(cls["stats"])
    # Level 1 HP is the top of the hit die, not a roll — no dice are drawn
    # here. The discarded roll this used to make still advanced the seeded
    # stream, so two runs on the same seed diverged from the first turn.
    player.hp_max = cls["hit_die_max"] + player.mod("con") + cls.get("hp_bonus", 0)
    player.hp = player.hp_max
    for grant in cls["abilities"]:
        if grant["level"] <= 1:
            player.abilities.append(grant["id"])
            player.cooldowns[grant["id"]] = content.ability(grant["id"]).get("uses", 1)
    state.player = player

    comp_spec = content.companions[companion_id]
    state.companion = Companion(cid=companion_id, hp=comp_spec["hp"],
                                hp_max=comp_spec["hp"])

    if name.strip().lower() in ("carl", "princess donut", "donut"):
        # A nod to the other, better-known dungeon crawler.
        state.flags["carl"] = True
        state.player.stats["con"] += 1
        state.player.hp_max += 4
        state.player.hp = state.player.hp_max

    for iid in cls.get("starting_items", []):
        state.add_item(iid)
    for slot, iid in cls.get("starting_equipment", {}).items():
        state.equipped[slot] = iid
    return state
