"""Recompute room `pos` from the exit graph.

Hand-placing coordinates for thirty-plus rooms and keeping them consistent with
the exits is a losing game. The exits are the truth; positions are derived.

    python3 tools/layout.py                 check every floor
    python3 tools/layout.py --fix 5 6       rewrite pos for those floors

A contradiction means the exit graph itself is impossible to draw on a grid
(walk east then south then west then north and end up somewhere else), which is
a real authoring bug and is reported rather than silently patched.
"""

import json
import os
import sys
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOOR_DIR = os.path.join(ROOT, "content", "floors")
DELTA = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def solve(floor):
    """BFS from the start room. Returns (positions, problems)."""
    rooms = floor["rooms"]
    start = floor["start"]
    pos = {start: (0, 0)}
    problems = []
    queue = deque([start])

    while queue:
        rid = queue.popleft()
        x, y = pos[rid]
        for direction, dest in rooms[rid].get("exits", {}).items():
            if dest not in rooms:
                problems.append(f"{rid} {direction} -> {dest} does not exist")
                continue
            back = rooms[dest].get("exits", {}).get(OPPOSITE[direction])
            if back != rid:
                problems.append(
                    f"{rid} {direction} -> {dest} is not mirrored "
                    f"({dest} {OPPOSITE[direction]} -> {back})")
            if direction not in DELTA:
                continue
            dx, dy = DELTA[direction]
            want = (x + dx, y + dy)
            if dest in pos:
                if pos[dest] != want:
                    problems.append(
                        f"{rid} {direction} -> {dest}: graph puts it at "
                        f"{want} and also at {pos[dest]}")
                continue
            pos[dest] = want
            queue.append(dest)

    unreached = sorted(set(rooms) - set(pos))
    for rid in unreached:
        problems.append(f"{rid} is unreachable from {start}")
    return pos, problems


def normalise(pos):
    """Shift so the top-left is (0, 0)."""
    if not pos:
        return {}
    min_x = min(p[0] for p in pos.values())
    min_y = min(p[1] for p in pos.values())
    return {rid: [x - min_x, y - min_y] for rid, (x, y) in pos.items()}


def process(path, fix):
    floor = json.load(open(path))
    pos, problems = solve(floor)
    name = os.path.basename(path)

    for problem in problems:
        print(f"  ERROR {name}: {problem}")

    if problems:
        return False

    grid = normalise(pos)
    changed = [rid for rid, xy in grid.items()
               if list(floor["rooms"][rid].get("pos", [])) != xy]
    if fix:
        for rid, xy in grid.items():
            floor["rooms"][rid]["pos"] = xy
        json.dump(floor, open(path, "w"), indent=2)
        print(f"  {name}: {len(floor['rooms'])} rooms, "
              f"{len(changed)} positions rewritten")
    else:
        state = "ok" if not changed else f"{len(changed)} positions wrong"
        print(f"  {name}: {len(floor['rooms'])} rooms, {state}")
    return True


def main():
    fix = "--fix" in sys.argv
    wanted = [a for a in sys.argv[1:] if a.isdigit()]
    ok = True
    for fname in sorted(os.listdir(FLOOR_DIR)):
        if not fname.endswith(".json"):
            continue
        if wanted and str(int(fname[:2])) not in wanted:
            continue
        ok &= process(os.path.join(FLOOR_DIR, fname), fix)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
