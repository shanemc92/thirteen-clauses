"""All mutable run state. Everything here must round-trip through JSON."""

from dataclasses import dataclass, field, asdict, fields

SCHEMA_VERSION = 1
PATH_HISTORY_MAX = 20
INVENTORY_CAP = 12

MODE_EXPLORE = "explore"
MODE_COMBAT = "combat"
MODE_MINIGAME = "minigame"
MODE_CHOICE = "choice"
MODE_DEAD = "dead"
MODE_SHOP = "shop"
MODE_WON = "won"


def _only_known(cls, d):
    """Keep just the fields `cls` actually declares.

    Save files are user-editable and arrive from other builds, so `cls(**d)`
    was one stray key away from a TypeError that escaped SaveError and took
    the session down. Unknown keys are dropped and missing ones fall back to
    their defaults, which makes an older or newer save load as far as it can
    rather than not at all.
    """
    if not isinstance(d, dict):
        return {}
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in known}


@dataclass
class Player:
    name: str = "Nobody"
    cls: str = "vanguard"
    level: int = 1
    xp: int = 0
    hp: int = 10
    hp_max: int = 10
    stats: dict = field(default_factory=lambda: {
        "str": 10, "dex": 10, "con": 10, "int": 10, "cha": 10})
    abilities: list = field(default_factory=list)
    cooldowns: dict = field(default_factory=dict)   # ability_id -> uses left
    statuses: dict = field(default_factory=dict)    # status -> rounds left
    death_saves: dict = field(default_factory=lambda: {"pass": 0, "fail": 0})
    hp_cost_pool: int = 0                           # Hexwright spent HP tracker

    def mod(self, stat: str) -> int:
        return (self.stats.get(stat, 10) - 10) // 2

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**_only_known(cls, d))


@dataclass
class Companion:
    cid: str = ""
    hp: int = 8
    hp_max: int = 8
    alive: bool = True
    cooldowns: dict = field(default_factory=dict)
    statuses: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**_only_known(cls, d))


@dataclass
class Combatant:
    uid: str
    monster_id: str
    name: str
    hp: int
    hp_max: int
    ac: int
    statuses: dict = field(default_factory=dict)
    elite: bool = False

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**_only_known(cls, d))


@dataclass
class Stalker:
    monster_id: str
    name: str
    distance: int = 3
    patience: int = 6
    hp_carried: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**_only_known(cls, d))


@dataclass
class Combat:
    enemies: list = field(default_factory=list)     # list[Combatant]
    order: list = field(default_factory=list)       # list of actor keys
    turn: int = 0
    round: int = 1
    surprised: bool = False
    fled: bool = False
    source_room: str = ""

    def to_dict(self):
        return {
            "enemies": [e.to_dict() for e in self.enemies],
            "order": self.order, "turn": self.turn, "round": self.round,
            "surprised": self.surprised, "fled": self.fled,
            "source_room": self.source_room,
        }

    @classmethod
    def from_dict(cls, d):
        known = _only_known(cls, d)
        known.pop("enemies", None)
        c = cls(**known)
        c.enemies = [Combatant.from_dict(e) for e in (d.get("enemies") or [])]
        return c

    def living(self):
        return [e for e in self.enemies if e.alive]

    def by_uid(self, uid):
        for e in self.enemies:
            if e.uid == uid:
                return e
        return None


@dataclass
class RunStats:
    kills: int = 0
    rooms_entered: int = 0
    chests_opened: int = 0
    nat20s: int = 0
    nat1s: int = 0
    damage_taken: int = 0
    damage_dealt: int = 0
    pounces_survived: int = 0
    minigames_won: int = 0
    trapdoors_fallen: int = 0
    notes_read: int = 0
    stashes_found: int = 0
    turns: int = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**_only_known(cls, d))


@dataclass
class GameState:
    content_version: str = ""
    schema_version: int = SCHEMA_VERSION
    seed: int = 0
    rng_counter: int = 0
    floor: int = 1
    room: str = ""
    mode: str = MODE_EXPLORE
    palette: str = "mono"
    player: Player = field(default_factory=Player)
    companion: Companion = field(default_factory=Companion)
    inventory: list = field(default_factory=list)   # [{"id":..,"qty":..}]
    equipped: dict = field(default_factory=dict)    # slot -> item_id
    flags: dict = field(default_factory=dict)
    visited: list = field(default_factory=list)
    path_history: list = field(default_factory=list)  # [[room_id, direction]]
    stalkers: list = field(default_factory=list)
    combat: object = None
    minigame: dict = None
    pending: dict = None                            # choice prompt payload
    log_tail: list = field(default_factory=list)
    stats: RunStats = field(default_factory=RunStats)
    # The narrator is not optional and there is only one of him. Muting him
    # and swapping commentary tracks were both choices that only made the
    # game harder to talk about, and picking one meant wondering about the
    # others for the rest of the run.
    continue_available: bool = False
    continue_used: bool = False
    settings: dict = field(default_factory=lambda: {"pace": "slow"})
    currency: int = 0
    inventory_bonus: int = 0
    keepsakes: list = field(default_factory=list)   # key items, no slot cost
    shop: dict = None

    # -- helpers ---------------------------------------------------------
    def push_path(self, room_id: str, direction: str):
        self.path_history.append([room_id, direction])
        if len(self.path_history) > PATH_HISTORY_MAX:
            self.path_history.pop(0)

    def log(self, text: str):
        self.log_tail.append(text)
        if len(self.log_tail) > 20:
            self.log_tail.pop(0)

    def cap(self) -> int:
        """Carrying capacity, including anything bought from the merchant."""
        return INVENTORY_CAP + self.inventory_bonus

    def has_item(self, item_id: str) -> bool:
        return (any(i["id"] == item_id for i in self.inventory)
                or item_id in self.keepsakes)

    def add_keepsake(self, item_id: str) -> bool:
        """Record a key item. Returns False if it was already held.

        Keepsakes do not take a carrying slot: they are trophies and proofs,
        not equipment, and there is no sense in a mug of your own face
        crowding out the thing that keeps you alive.
        """
        if item_id in self.keepsakes:
            return False
        self.keepsakes.append(item_id)
        return True

    def add_item(self, item_id: str, qty: int = 1) -> bool:
        for entry in self.inventory:
            if entry["id"] == item_id:
                entry["qty"] += qty
                return True
        if len(self.inventory) >= self.cap():
            return False
        self.inventory.append({"id": item_id, "qty": qty})
        return True

    def remove_item(self, item_id: str, qty: int = 1) -> bool:
        for entry in list(self.inventory):
            if entry["id"] == item_id:
                entry["qty"] -= qty
                if entry["qty"] <= 0:
                    self.inventory.remove(entry)
                return True
        return False

    def inventory_full(self) -> bool:
        return len(self.inventory) >= self.cap()

    # -- persistence -----------------------------------------------------
    def to_dict(self):
        return {
            "content_version": self.content_version,
            "schema_version": self.schema_version,
            "seed": self.seed, "rng_counter": self.rng_counter,
            "floor": self.floor, "room": self.room, "mode": self.mode,
            "palette": self.palette,
            "player": self.player.to_dict(),
            "companion": self.companion.to_dict(),
            "inventory": self.inventory, "equipped": self.equipped,
            "flags": self.flags, "visited": self.visited,
            "path_history": self.path_history,
            "stalkers": [s.to_dict() for s in self.stalkers],
            "combat": self.combat.to_dict() if self.combat else None,
            "minigame": self.minigame, "pending": self.pending,
            "log_tail": self.log_tail, "stats": self.stats.to_dict(),
            "continue_available": self.continue_available,
            "continue_used": self.continue_used,
            "settings": self.settings,
            "currency": self.currency, "shop": self.shop,
            "inventory_bonus": self.inventory_bonus,
            "keepsakes": self.keepsakes,
        }

    @classmethod
    def from_dict(cls, d):
        state = cls()
        for key in ("content_version", "schema_version", "seed", "rng_counter",
                    "floor", "room", "mode", "palette", "inventory", "equipped",
                    "flags", "visited", "path_history", "minigame", "pending",
                    "log_tail", "continue_available", "continue_used",
                    "settings", "currency", "shop", "inventory_bonus",
                    "keepsakes"):
            if key in d:
                setattr(state, key, d[key])
        state.player = Player.from_dict(d.get("player") or {})
        state.companion = Companion.from_dict(d.get("companion") or {})
        state.stalkers = [Stalker.from_dict(s) for s in d.get("stalkers", [])]
        state.combat = Combat.from_dict(d["combat"]) if d.get("combat") else None
        state.stats = RunStats.from_dict(d.get("stats", {}))
        return state
