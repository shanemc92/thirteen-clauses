"""Dice notation: 2d6+3, 1d8, 4d4-1, 3 (flat)."""

import re
from dataclasses import dataclass

PATTERN = re.compile(r"^\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)
_cache: dict[str, "Dice"] = {}


@dataclass(frozen=True)
class Dice:
    count: int
    sides: int
    mod: int = 0

    def __str__(self) -> str:
        if self.sides == 0:
            return str(self.mod)
        base = f"{self.count}d{self.sides}"
        if self.mod > 0:
            return f"{base}+{self.mod}"
        if self.mod < 0:
            return f"{base}{self.mod}"
        return base

    def roll(self, rng, crit: bool = False) -> tuple[list[int], int]:
        """Returns (individual rolls, total). Crit doubles dice, not the mod."""
        if self.sides == 0:
            return [], self.mod
        n = self.count * 2 if crit else self.count
        rolls = [rng.d(self.sides) for _ in range(n)]
        return rolls, max(0, sum(rolls) + self.mod)

    def average(self) -> float:
        if self.sides == 0:
            return float(self.mod)
        return self.count * (self.sides + 1) / 2 + self.mod


def parse(notation) -> Dice:
    if isinstance(notation, Dice):
        return notation
    if isinstance(notation, int):
        return Dice(0, 0, notation)
    key = str(notation).strip()
    if key in _cache:
        return _cache[key]
    match = PATTERN.match(key)
    if not match:
        if key.lstrip("+-").isdigit():
            dice = Dice(0, 0, int(key))
            _cache[key] = dice
            return dice
        raise ValueError(f"bad dice notation: {notation!r}")
    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    mod = int(match.group(3).replace(" ", "")) if match.group(3) else 0
    dice = Dice(count, sides, mod)
    _cache[key] = dice
    return dice


def roll(notation, rng, crit: bool = False) -> tuple[list[int], int]:
    return parse(notation).roll(rng, crit)
