"""Deterministic, serialisable RNG.

State is (seed, counter). Any (seed, counter) pair reproduces the exact same
stream, so saving the counter means reloading cannot re-roll a bad result.
Pure stdlib arithmetic, no `random` import anywhere in the engine.
"""

MASK = (1 << 64) - 1
GOLDEN = 0x9E3779B97F4A7C15


def _splitmix64(x: int) -> int:
    x = (x + GOLDEN) & MASK
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
    return z ^ (z >> 31)


class Rng:
    def __init__(self, seed: int, counter: int = 0):
        self.seed = seed & MASK
        self.counter = counter

    # -- core ------------------------------------------------------------
    def _next(self) -> int:
        val = _splitmix64(self.seed + (self.counter * GOLDEN))
        self.counter += 1
        return val

    def peek(self) -> int:
        """Next value without advancing. For previews and UI hints."""
        return _splitmix64(self.seed + (self.counter * GOLDEN))

    # -- helpers ---------------------------------------------------------
    def randint(self, low: int, high: int) -> int:
        """Inclusive both ends."""
        if high < low:
            low, high = high, low
        return low + (self._next() % (high - low + 1))

    def d(self, sides: int) -> int:
        return self.randint(1, sides)

    def chance(self, p: float) -> bool:
        return (self._next() % 10000) < int(p * 10000)

    def choice(self, seq):
        seq = list(seq)
        if not seq:
            return None
        return seq[self._next() % len(seq)]

    def weighted(self, pairs) -> object:
        """pairs: iterable of (value, weight)."""
        pairs = [(v, w) for v, w in pairs if w > 0]
        if not pairs:
            return None
        total = sum(w for _, w in pairs)
        roll = self._next() % total
        for value, weight in pairs:
            if roll < weight:
                return value
            roll -= weight
        return pairs[-1][0]

    def shuffled(self, seq):
        items = list(seq)
        for i in range(len(items) - 1, 0, -1):
            j = self._next() % (i + 1)
            items[i], items[j] = items[j], items[i]
        return items

    # -- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        return {"seed": self.seed, "counter": self.counter}

    @classmethod
    def from_dict(cls, data: dict) -> "Rng":
        return cls(data["seed"], data["counter"])

    def fork(self, tag: str) -> "Rng":
        """Independent substream. Does not advance the parent."""
        mixed = _splitmix64(self.seed ^ (hash_str(tag) & MASK))
        return Rng(mixed, 0)


def hash_str(text: str) -> int:
    """Stable across runs, unlike builtin hash()."""
    acc = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        acc = ((acc ^ byte) * 0x100000001B3) & MASK
    return acc
