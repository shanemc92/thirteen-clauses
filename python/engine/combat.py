"""Combat. d20 to hit, dice damage, one action per turn.

Every roll emits a DiceRolled event. Watching the dice is half the appeal, so
nothing here is allowed to resolve silently.
"""

from . import events as ev
from . import dice
from .state import Combat, Combatant, MODE_COMBAT, MODE_EXPLORE, MODE_DEAD

UNARMED = {"dmg": "1d4", "stat": "str", "name_key": "combat.unarmed"}

# Flavour picks must not consume the combat RNG stream, or a save reloaded
# mid-fight would diverge. This is cosmetic only.
class _FlavourRng:
    def __init__(self):
        self.n = 0

    def choice(self, seq):
        seq = list(seq)
        if not seq:
            return None
        self.n = (self.n * 1103515245 + 12345) & 0x7FFFFFFF
        return seq[self.n % len(seq)]


_DEATH_RNG = _FlavourRng()


# ---------------------------------------------------------------- derived
def proficiency(player) -> int:
    return 2 + (player.level - 1) // 4


def weapon(state, content) -> dict:
    wid = state.equipped.get("weapon")
    if not wid:
        return {**UNARMED, "name": content.t("combat.unarmed")}
    item = content.item(wid)
    return {"dmg": item.get("dmg", "1d4"),
            "stat": item.get("stat", "str"),
            "name": content.t(item["name_key"])}


def player_ac(state, content) -> int:
    base = 10 + state.player.mod("dex")
    cls = content.classes[state.player.cls]
    base += cls.get("ac_bonus", 0)
    aid = state.equipped.get("armour")
    if aid:
        base += content.item(aid).get("ac", 0)
    if "guarded" in state.player.statuses:
        base += 2
    return base


def player_name(state) -> str:
    return state.player.name


# ---------------------------------------------------------------- rolling
def _roll_d20(rng, modifier, purpose, advantage=False, disadvantage=False):
    """Returns (total, natural, event)."""
    first = rng.d(20)
    rolls = [first]
    natural = first
    if advantage != disadvantage:
        second = rng.d(20)
        rolls.append(second)
        natural = max(rolls) if advantage else min(rolls)
    total = natural + modifier
    event = ev.dice_rolled(
        formula=f"d20{modifier:+d}", rolls=rolls, modifier=modifier,
        total=total, purpose=purpose, crit=(natural == 20), fumble=(natural == 1))
    return total, natural, event


# ---------------------------------------------------------------- setup
# A senior is bigger, not harder to hit. The +1 AC was the single most
# expensive point in the game: against an elite at AC 22 a geared player
# connected about 40% of the time, so the fight ran 22 rounds and the senior
# won the exchange by a hair the whole way through. Removing it, and taking
# two points off the deep mini bosses, moved Floor 11 from fifteen potions
# over fifty rounds to two over fifteen.
ELITE_HP = 1.3


def spawn(content, rng, monster_id, index=0, elite=False) -> Combatant:
    mon = content.monster(monster_id)
    low, high = mon["hp"]
    hp = rng.randint(low, high)
    ac = mon["ac"]
    if elite:
        # 1.6 was tuned when elites were ordinary monsters promoted. The deep
        # floors give the role to bespoke mini bosses that are already about
        # twice an ordinary monster, so the two scalings compounded and a
        # geared level 17 player died to every senior from Floor 10 down.
        # A senior is bigger, not harder to hit: HP scales, AC does not.
        hp = int(hp * ELITE_HP)
    name = content.t(mon["name_key"])
    if elite:
        name = content.t("labels.elite_prefix") + " " + name
    return Combatant(uid=f"{monster_id}#{index}", monster_id=monster_id,
                     name=name, hp=hp, hp_max=hp, ac=ac, elite=elite)


def start_combat(state, content, rng, monster_ids, surprised=False,
                 elite=False, carried_hp=None, source_room="", bonus_hp=0):
    out = []
    enemies = [spawn(content, rng, mid, i, elite=elite)
               for i, mid in enumerate(monster_ids)]
    _number_duplicates(enemies)
    if bonus_hp:
        # Applied to the first enemy, before carried_hp, so a restarted
        # fight resumes at whatever health it was actually left on rather
        # than picking the bonus up a second time.
        enemies[0].hp_max += bonus_hp
        enemies[0].hp = enemies[0].hp_max
    if carried_hp:
        enemies[0].hp = max(1, carried_hp)
    for enemy in enemies:
        mon = content.monster(enemy.monster_id)
        if mon.get("mirror"):
            _apply_mirror(state, content, enemy)
        if mon.get("scales_with_floor"):
            k = 1.0 + 0.18 * max(0, state.floor - 2)
            enemy.hp_max = int(enemy.hp_max * k)
            enemy.hp = enemy.hp_max
            enemy.ac += state.floor // 4
        pair = mon.get("paired_with")
        if pair and not state.flags.get(f"defeated.{pair}"):
            # Its other half is still standing somewhere on this floor and
            # they are, in the clause's own words, not severable.
            enemy.hp_max = int(enemy.hp_max * 1.5)
            enemy.hp = enemy.hp_max
            enemy.ac += 2
            out.append(ev.plain(content.t("combat.paired_intact",
                                          name=enemy.name)))
    combat = Combat(enemies=enemies, surprised=surprised, source_room=source_room)

    approach_key = "combat.approach_surprise" if surprised else "combat.approach"
    out.append(ev.combat_approaching(content.t(approach_key, rng)))

    shown = set()
    for enemy in enemies:
        if enemy.monster_id in shown:
            continue
        shown.add(enemy.monster_id)
        art = content.get_art(content.monster(enemy.monster_id).get("art", ""))
        if art:
            out.append(ev.art_shown(enemy.monster_id, art))

    out.append(ev.combat_started([e.name for e in enemies], surprised))
    out.append(round_event(state, content, combat))

    if not state.flags.get("tut.combat"):
        state.flags["tut.combat"] = True
        out.append(ev.block(content.t("help.combat_primer")))

    # Initiative
    order = []
    pinit, _, pev = _roll_d20(rng, state.player.mod("dex"), "initiative")
    out.append(pev)
    order.append(("player", pinit))
    if state.companion.alive and state.companion.cid:
        comp = content.companions[state.companion.cid]
        order.append(("companion", rng.d(20) + comp.get("dex_mod", 0)))
    for enemy in enemies:
        mon = content.monster(enemy.monster_id)
        order.append((f"e:{enemy.uid}", rng.d(20) + mon.get("dex_mod", 0)))
    order.sort(key=lambda pair: -pair[1])
    combat.order = [key for key, _ in order]

    if surprised:
        # Player and companion lose the first round.
        combat.order = [k for k in combat.order if k.startswith("e:")] + \
                       [k for k in combat.order if not k.startswith("e:")]
    state.flags.pop("comp_rescue_used", None)
    state.combat = combat
    state.mode = MODE_COMBAT
    spoken = set()
    for enemy in enemies:
        if enemy.monster_id in spoken:
            continue
        spoken.add(enemy.monster_id)
        mon = content.monster(enemy.monster_id)
        if mon.get("taunts"):
            out.append(ev.speech(enemy.name, content.t(rng.choice(mon["taunts"]), rng)))
    return out


# ---------------------------------------------------------------- damage
def _apply_mirror(state, content, enemy):
    """A boss that fights as a copy of you. Built from your sheet, not a stat block."""
    # It used to be three times your health at two better than your armour,
    # which no amount of play could get through. It mirrors you now, rather
    # than exceeding you: your armour, and about half again your health.
    enemy.ac = max(enemy.ac, player_ac(state, content))
    enemy.hp_max = max(enemy.hp_max, int(state.player.hp_max * 1.5))
    enemy.hp = enemy.hp_max
    return enemy


def _number_duplicates(enemies):
    """Two of the same thing need telling apart. One of a thing does not."""
    counts = {}
    for enemy in enemies:
        counts[enemy.name] = counts.get(enemy.name, 0) + 1
    seen = {}
    for enemy in enemies:
        if counts[enemy.name] > 1:
            seen[enemy.name] = seen.get(enemy.name, 0) + 1
            enemy.name = f"{enemy.name} {seen[enemy.name]}"


def resolve_target(combat, arg):
    """Accepts a uid, a 1-based index over living enemies, or a name fragment."""
    living = combat.living()
    if not living:
        return None
    arg = (arg or "").strip().lower()
    if not arg:
        return living[0]
    exact = combat.by_uid(arg)
    if exact and exact.alive:
        return exact
    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(living):
            return living[idx]
        return living[0]
    for enemy in living:
        if enemy.name.lower() == arg:
            return enemy
    for enemy in living:
        if arg in enemy.name.lower():
            return enemy
    return living[0]


def roster(combat):
    return [{"name": e.name, "hp": e.hp, "hp_max": e.hp_max,
             "index": i + 1, "statuses": list(e.statuses)}
            for i, e in enumerate(combat.living())]


def side_status(state, content):
    """Your half of the board: you, and whoever came with you.

    Emitted every round so the fight can be read without opening the sheet.
    """
    player = {"name": state.player.name,
              "hp": max(0, state.player.hp), "hp_max": state.player.hp_max,
              "ac": player_ac(state, content),
              "downed": bool(state.flags.get("downed")),
              "statuses": list(state.player.statuses)}
    companion = None
    if state.companion.cid:
        spec = content.companions[state.companion.cid]
        companion = {"name": content.t(spec["name_key"]),
                     "hp": max(0, state.companion.hp),
                     "hp_max": state.companion.hp_max,
                     "alive": state.companion.alive}
    return player, companion


def round_event(state, content, combat):
    player, companion = side_status(state, content)
    return ev.round_started(combat.round, roster(combat), player, companion)


def _damage_enemy(state, content, enemy, amount, out):
    """Applies damage. Returns trailing events to emit AFTER the hit line,
    so the kill reads as the consequence of the blow rather than preceding it."""
    if "vulnerable" in enemy.statuses:
        amount += 2

    mon = content.monster(enemy.monster_id)
    if (mon.get("indemnify") and state.companion.alive
            and state.companion.hp > state.companion.hp_max // 4):
        share = max(1, amount // 3)
        _damage_companion(state, content, share, out)
        out.append(ev.plain(content.t("combat.indemnified", amount=share)))
    cap = mon.get("damage_cap")
    if cap and not state.flags.get(mon.get("cap_break_flag", "")):
        if amount > cap:
            out.append(ev.plain(content.t("combat.capped",
                                          name=enemy.name, was=amount, cap=cap)))
            amount = cap
    enemy.hp -= amount
    state.stats.damage_dealt += amount
    if enemy.hp > 0:
        phase = mon.get("phase_minigame")
        if (phase and not state.flags.get(f"phase.{enemy.uid}")
                and enemy.hp <= enemy.hp_max * phase.get("at_hp_pct", 0.5)):
            state.flags[f"phase.{enemy.uid}"] = True
            state.flags["pending_phase"] = dict(phase, uid=enemy.uid,
                                                name=enemy.name)
        return []
    enemy.hp = 0
    state.stats.kills += 1
    key = f"mon.{enemy.monster_id}.death"
    if content.raw(key) is None:
        key = "combat.defeated"
    events = [ev.defeated(enemy.name,
                          content.t(key, _DEATH_RNG, name=enemy.name))]
    # A death the page should mark: the Reaper's own text describes forty
    # years of shredded records coming down like slow grey snow.
    fx_spec = ON_DEATH_EFFECT.get(enemy.monster_id)
    if fx_spec:
        events.append(ev.effect(fx_spec[0], seconds=fx_spec[1]))
    return events


# Deaths the page should mark. The Reaper's own text describes forty
# years of shredded records coming down like slow grey snow.
ON_DEATH_EFFECT = {"reaper_of_records": ("ember", 8.0)}


def hurt_player(state, content, amount, out, source=""):
    """Damage from outside a fight: traps, events, the building being unkind."""
    _damage_player(state, content, amount, out, source)


def _fire_resisted(state, content, amount, source):
    """Bartleby's fire resistance, which covers you as well as him."""
    comp = state.companion
    if not (comp.cid and comp.alive) or "fire" not in (source or ""):
        return amount, False
    factor = content.companions[comp.cid].get("fire_resist")
    if not factor:
        return amount, False
    return max(1, int(amount * factor)), True


def _damage_player(state, content, amount, out, source=""):
    amount, resisted = _fire_resisted(state, content, amount, source)
    if resisted:
        out.append(ev.plain(content.t(
            "combat.fire_resisted",
            name=content.t(content.companions[state.companion.cid]["name_key"]))))
    state.player.hp -= amount
    state.stats.damage_taken += amount
    if state.player.hp <= 0:
        state.player.hp = 0
        if not state.flags.get("downed"):
            state.flags["downed"] = True
            state.player.death_saves = {"pass": 0, "fail": 0}
            out.append(ev.status_changed(player_name(state), "downed", True))


def _damage_companion(state, content, amount, out):
    comp = state.companion
    comp.hp -= amount
    if comp.hp <= 0:
        comp.hp = 0
        comp.alive = False
        name = content.t(content.companions[comp.cid]["name_key"])
        out.append(ev.status_changed(name, "out of service", True))
        out.append(ev.plain(content.t("companion.down", name=name)))


# ---------------------------------------------------------------- actions
def player_attack(state, content, rng, target_uid=""):
    combat = state.combat
    out = []
    living = combat.living()
    if not living:
        return out
    target = resolve_target(combat, target_uid)

    wep = weapon(state, content)
    stat = wep["stat"]
    modifier = state.player.mod(stat) + proficiency(state.player)
    advantage = "advantage" in state.player.statuses
    disadvantage = "weakened" in state.player.statuses
    total, natural, roll_ev = _roll_d20(
        rng, modifier, f"attack {target.name}", advantage, disadvantage)
    out.append(roll_ev)
    state.player.statuses.pop("advantage", None)

    if natural == 1:
        state.stats.nat1s += 1
        out.append(ev.attack_resolved(player_name(state), target.name,
                                      False, False, 0,
                                      content.t("combat.fumble", rng)))
        return out
    from . import quirks
    crit_at = quirks.crit_range(content, state)
    if state.player.cls == "skirmisher":
        crit_at = min(crit_at, 19)
    if natural >= crit_at:
        state.stats.nat20s += 1

    crit = natural >= crit_at
    if not crit and total < target.ac:
        out.append(ev.attack_resolved(player_name(state), target.name,
                                      False, False, 0,
                                      content.t("combat.miss", rng)))
        return out

    rolls, amount = dice.roll(wep["dmg"], rng, crit)
    amount += state.player.mod(stat)
    amount = max(1, amount)
    out.append(ev.dice_rolled(str(dice.parse(wep["dmg"])), rolls,
                              state.player.mod(stat), amount,
                              f"damage with {wep['name']}", crit=crit))
    trailing = _damage_enemy(state, content, target, amount, out)
    out.append(ev.attack_resolved(player_name(state), target.name, True, crit, amount))
    out.extend(trailing)
    return out


def use_ability(state, content, rng, ability_id, target_uid=""):
    out = []
    player = state.player
    if ability_id not in player.abilities:
        return [ev.error(content.t("errors.no_ability"))]
    spec = content.ability(ability_id)
    left = player.cooldowns.get(ability_id, spec.get("uses", 1))
    if left <= 0:
        return [ev.error(content.t("errors.ability_spent",
                                   name=content.t(spec["name_key"])))]

    cost = spec.get("hp_cost", 0)
    if cost:
        if player.hp <= cost:
            return [ev.error(content.t("errors.hp_cost"))]
        player.hp -= cost
        out.append(ev.plain(content.t("combat.hp_cost", amount=cost)))

    player.cooldowns[ability_id] = left - 1
    out.append(ev.speech(player_name(state),
                         content.t(spec.get("flavour_key", ""), rng)))

    combat = state.combat
    living = combat.living() if combat else []
    target = resolve_target(combat, target_uid) if living else None

    for effect in spec["effects"]:
        out.extend(_apply_effect(state, content, rng, effect, target, spec))
    return out


def _apply_effect(state, content, rng, effect, target, spec):
    out = []
    op = effect["op"]
    combat = state.combat

    if op == "damage" and target:
        rolls, amount = dice.roll(effect["dice"], rng)
        if effect.get("stat"):
            amount += state.player.mod(effect["stat"])
        out.append(ev.dice_rolled(effect["dice"], rolls, 0, amount,
                                  f"{content.t(spec['name_key'])} damage"))
        trailing = _damage_enemy(state, content, target, amount, out)
        out.append(ev.attack_resolved(state.player.name, target.name,
                                      True, False, amount))
        out.extend(trailing)

    elif op == "damage_all":
        for enemy in (combat.living() if combat else []):
            rolls, amount = dice.roll(effect["dice"], rng)
            out.append(ev.dice_rolled(effect["dice"], rolls, 0, amount,
                                      f"{enemy.name} takes it"))
            out.extend(_damage_enemy(state, content, enemy, amount, out))

    elif op == "heal":
        rolls, amount = dice.roll(effect["dice"], rng)
        before = state.player.hp
        state.player.hp = min(state.player.hp_max, state.player.hp + amount)
        out.append(ev.dice_rolled(effect["dice"], rolls, 0, amount, "healing"))
        out.append(ev.plain(content.t("combat.healed",
                                      amount=state.player.hp - before,
                                      hp=state.player.hp, max=state.player.hp_max)))

    elif op == "status_self":
        state.player.statuses[effect["status"]] = effect.get("rounds", 1)
        out.append(ev.status_changed(state.player.name, effect["status"],
                                     True, effect.get("rounds", 1)))

    elif op == "status_enemy" and target:
        target.statuses[effect["status"]] = effect.get("rounds", 1)
        out.append(ev.status_changed(target.name, effect["status"],
                                     True, effect.get("rounds", 1)))

    elif op == "status_all":
        for enemy in (combat.living() if combat else []):
            enemy.statuses[effect["status"]] = effect.get("rounds", 1)
            out.append(ev.status_changed(enemy.name, effect["status"],
                                         True, effect.get("rounds", 1)))

    elif op == "cancel":
        state.flags["objection_ready"] = True
        out.append(ev.plain(content.t("combat.objection_armed")))

    elif op == "escape":
        state.flags["slip_ready"] = True
        out.append(ev.plain(content.t("combat.slip_armed")))

    return out


def player_flee(state, content, rng):
    """Escape check. Failure costs the turn, success turns enemies into stalkers."""
    from .stalker import add_stalker
    out = []
    combat = state.combat
    living = combat.living()
    hardest = max((content.monster(e.monster_id).get("dex_mod", 0) for e in living),
                  default=0)
    from . import quirks
    dc = 10 + hardest + quirks.flee_dc_bonus(content, state)
    if state.flags.pop("slip_ready", False):
        out.append(ev.plain(content.t("combat.slip_used")))
        total, natural = dc + 1, 20
        out.append(ev.plain(content.t("combat.flee_ok", rng)))
    else:
        total, natural, roll_ev = _roll_d20(rng, state.player.mod("dex"),
                                            f"escape (DC {dc})")
        out.append(roll_ev)
    if total >= dc:
        for enemy in living:
            mon = content.monster(enemy.monster_id)
            if mon.get("stalker", {}).get("enabled"):
                add_stalker(state, content, enemy, out)
        combat.fled = True
        if state.flags.pop("downed", False):
            state.player.death_saves = {"pass": 0, "fail": 0}
            state.player.hp = max(1, state.player.hp)
        out.append(ev.combat_ended("fled"))
        state.combat = None
        state.mode = MODE_EXPLORE
        return out
    out.append(ev.plain(content.t("combat.flee_fail", rng)))
    return out


# ---------------------------------------------------------------- NPC turns
def _summon(state, content, rng, enemy, out):
    """A boss that calls for help. Returns True if it spent its turn calling."""
    mon = content.monster(enemy.monster_id)
    spec = mon.get("summons")
    if not spec:
        return False
    combat = state.combat
    called = state.flags.get(f"summoned.{enemy.uid}", 0)
    if called >= spec.get("max", 2):
        return False
    if combat.round < spec.get("first_round", 2):
        return False
    if (combat.round - spec.get("first_round", 2)) % spec.get("every", 2) != 0:
        return False
    if len(combat.living()) >= spec.get("field_cap", 4):
        return False

    state.flags[f"summoned.{enemy.uid}"] = called + 1
    helpers = []
    for i in range(spec.get("count", 1)):
        helper = spawn(content, rng, spec["monster"],
                       index=100 + called * 10 + i)
        helpers.append(helper)
        combat.enemies.append(helper)
        combat.order.append(f"e:{helper.uid}")
    _number_duplicates(combat.enemies)
    out.append(ev.speech(enemy.name, content.t(spec["line_key"], rng)))
    out.append(ev.plain(content.t("combat.reinforcements",
                                  names=", ".join(h.name for h in helpers))))
    out.append(round_event(state, content, combat))
    return True


def _enemy_turn(state, content, rng, enemy):
    out = []
    if not enemy.alive:
        return out
    mon = content.monster(enemy.monster_id)
    if _summon(state, content, rng, enemy, out):
        return out

    if "stunned" in enemy.statuses:
        out.append(ev.plain(content.t("combat.stunned", name=enemy.name)))
        return out

    attack = rng.choice(mon["attacks"])
    to_hit = attack["to_hit"]
    if "weakened" in enemy.statuses:
        to_hit -= 2

    # Without this the companion only ever gets hit by a taunt or the Floor 8
    # indemnity, so it sat permanently at full health and its HP bar meant
    # nothing. A quarter of attacks go its way instead.
    comp_alive = state.companion.alive and bool(state.companion.cid)
    taunted = "taunted" in enemy.statuses and comp_alive
    at_companion = taunted or (comp_alive and rng.chance(0.12))

    if at_companion:
        comp_spec = content.companions[state.companion.cid]
        target_name = content.t(comp_spec["name_key"])
        target_ac = comp_spec.get("ac", 13)
    else:
        target_name = player_name(state)
        target_ac = player_ac(state, content)

    total, natural, roll_ev = _roll_d20(
        rng, to_hit, f"{enemy.name} attacks {target_name}")
    out.append(roll_ev)

    if natural == 1 or (natural != 20 and total < target_ac):
        out.append(ev.attack_resolved(enemy.name, target_name, False, False, 0,
                                      content.t("combat.enemy_miss", rng)))
        return out

    crit = natural == 20
    rolls, amount = dice.roll(attack["dmg"], rng, crit)
    out.append(ev.dice_rolled(attack["dmg"], rolls, 0, amount,
                              f"{enemy.name} damage", crit=crit))

    # Vanguard Interpose redirects a companion-bound hit.
    dmg_type = attack.get("type", "")
    if at_companion and "interposing" in state.player.statuses:
        out.append(ev.plain(content.t("combat.interposed")))
        _damage_player(state, content, max(1, amount // 2), out, dmg_type)
    elif at_companion:
        _damage_companion(state, content, amount, out)
    elif "interposing" in state.player.statuses:
        out.append(ev.plain(content.t("combat.interposed")))
        _damage_player(state, content, max(1, amount // 2), out, dmg_type)
    else:
        _damage_player(state, content, amount, out, dmg_type)
    out.append(ev.attack_resolved(enemy.name, target_name, True, crit, amount))
    return out


def _companion_turn(state, content, rng):
    out = []
    comp = state.companion
    if not comp.cid:
        return out
    spec = content.companions[comp.cid]
    name = content.t(spec["name_key"])
    breath = spec.get("breath")

    if not comp.alive:
        # Out of service: no attacks. One last favour, if it has one left.
        if (state.flags.get("downed") and spec.get("can_stabilise")
                and not state.flags.get("comp_rescue_used")):
            state.flags["comp_rescue_used"] = True
            out.append(ev.speech(name, content.t(spec["stabilise_key"], rng)))
            out.append(ev.plain(content.t("combat.crawl_rescue", name=name)))
            _bring_up(state, content, out)
        return out

    combat = state.combat
    living = combat.living()
    if not living:
        return out

    if (state.flags.get("downed") and spec.get("can_stabilise")
            and not state.flags.get("comp_rescue_used")):
        state.flags["comp_rescue_used"] = True
        out.append(ev.speech(name, content.t(spec["stabilise_key"], rng)))
        _bring_up(state, content, out)
        return out

    # Bartleby's breath, which he insists is full-sized.
    if breath and state.combat.round % breath.get("every", 3) == 0:
        rolls, amount = dice.roll(breath["dice"], rng)
        out.append(ev.speech(name, content.t(breath["key"], rng)))
        out.append(ev.plain(content.t("combat.breath_hits",
                                      name=name, amount=amount)))
        for enemy in list(living):
            _damage_enemy(state, content, enemy, amount, out)
        return out

    target = living[0]
    total, natural, roll_ev = _roll_d20(rng, spec.get("to_hit", 3),
                                        f"{name} attacks {target.name}")
    out.append(roll_ev)
    if natural == 1 or (natural != 20 and total < target.ac):
        out.append(ev.attack_resolved(name, target.name, False, False, 0,
                                      content.t("combat.enemy_miss", rng)))
        return out
    rolls, amount = dice.roll(spec.get("dmg", "1d4"), rng, natural == 20)
    out.append(ev.dice_rolled(spec.get("dmg", "1d4"), rolls, 0, amount,
                              f"{name} damage"))
    trailing = _damage_enemy(state, content, target, amount, out)
    out.append(ev.attack_resolved(name, target.name, True, natural == 20, amount))
    out.extend(trailing)
    return out


def _bring_up(state, content, out):
    """Back on your feet with enough health to take an action."""
    state.flags.pop("downed", None)
    state.player.death_saves = {"pass": 0, "fail": 0}
    state.player.hp = max(1, state.player.hp_max // 4)
    out.append(ev.plain(content.t("combat.stabilised", hp=state.player.hp)))


def _death_save(state, content, rng):
    out = []
    total, natural, roll_ev = _roll_d20(rng, 0, "death save (DC 10)")
    out.append(roll_ev)
    saves = state.player.death_saves
    if natural == 20:
        _bring_up(state, content, out)
        out.append(ev.plain(content.t("combat.death_save_nat20")))
        return out
    if total >= 10:
        saves["pass"] += 1
        out.append(ev.plain(content.t("combat.death_save_pass",
                                      n=saves["pass"])))
        if saves["pass"] >= 3:
            _bring_up(state, content, out)
    else:
        saves["fail"] += 1
        out.append(ev.plain(content.t("combat.death_save_fail",
                                      n=saves["fail"])))
    return out


def _tick_statuses(holder_statuses):
    for key in list(holder_statuses):
        if key.startswith("_"):
            continue
        holder_statuses[key] -= 1
        if holder_statuses[key] <= 0:
            del holder_statuses[key]


def advance(state, content, rng):
    """Run turns until it is the player's turn again, or combat ends.

    Returns (events, awaiting_player).
    """
    out = []
    combat = state.combat
    if combat is None:
        return out, False

    while True:
        if not combat.living():
            return out, False
        if state.player.death_saves["fail"] >= 3:
            state.mode = MODE_DEAD
            return out, False

        combat.turn += 1
        if combat.turn >= len(combat.order):
            combat.turn = 0
            combat.round += 1
            combat.surprised = False
            _tick_statuses(state.player.statuses)
            for enemy in combat.enemies:
                _tick_statuses(enemy.statuses)
            for enemy in combat.living():
                mon = content.monster(enemy.monster_id)
                if mon.get("steals_abilities"):
                    spendable = [a for a in state.player.abilities
                                 if state.player.cooldowns.get(a, 0) > 0]
                    if spendable:
                        taken = rng.choice(spendable)
                        state.player.cooldowns[taken] -= 1
                        healed = min(mon.get("steal_heal", 14),
                                     enemy.hp_max - enemy.hp)
                        enemy.hp += healed
                        out.append(ev.plain(content.t(
                            "combat.ability_stolen",
                            name=enemy.name,
                            ability=content.t(content.ability(taken)["name_key"]),
                            amount=healed)))
                chaotic = mon.get("chaotic")
                if chaotic:
                    low, high = chaotic.get("ac_range", [enemy.ac, enemy.ac])
                    enemy.ac = rng.randint(low, high)
                    out.append(ev.plain(content.t("combat.chaotic",
                                                  name=enemy.name, ac=enemy.ac)))
                amount = mon.get("regen", 0)
                if not amount:
                    continue
                if state.flags.get(mon.get("regen_break_flag", "")):
                    continue
                if enemy.hp >= enemy.hp_max:
                    continue
                # Finite rebuilds. Without a cap, a player who cannot
                # out-damage the regen is locked in a fight that never ends
                # and never kills them, which is worse than losing.
                limit = mon.get("regen_max", 6)
                used = enemy.statuses.get("_rebuilt", 0)
                if used >= limit:
                    if used == limit:
                        enemy.statuses["_rebuilt"] = used + 1
                        out.append(ev.plain(content.t("combat.regen_spent",
                                                      name=enemy.name)))
                    continue
                enemy.statuses["_rebuilt"] = used + 1
                healed = min(amount, enemy.hp_max - enemy.hp)
                enemy.hp += healed
                out.append(ev.plain(content.t(
                    "combat.regen", name=enemy.name, amount=healed,
                    left=limit - used - 1)))
            out.append(round_event(state, content, combat))

        actor = combat.order[combat.turn]

        if actor == "player":
            if state.flags.get("downed"):
                out.append(ev.turn_started(player_name(state), "player",
                                           combat.round))
                out.extend(_death_save(state, content, rng))
                if state.player.death_saves["fail"] >= 3:
                    state.mode = MODE_DEAD
                    return out, False
                if not state.flags.get("downed"):
                    return out, True      # up again: take your turn
                continue
            return out, True

        if actor == "companion":
            comp_name = content.t(
                content.companions[state.companion.cid]["name_key"])
            out.append(ev.turn_started(comp_name, "companion", combat.round))
            out.extend(_companion_turn(state, content, rng))
            continue

        enemy = combat.by_uid(actor[2:])
        if enemy and enemy.alive:
            out.append(ev.turn_started(enemy.name, "enemy", combat.round))
            out.extend(_enemy_turn(state, content, rng, enemy))
            if state.player.death_saves["fail"] >= 3:
                state.mode = MODE_DEAD
                return out, False


def is_over(state) -> bool:
    return state.combat is None or not state.combat.living()
