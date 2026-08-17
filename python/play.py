#!/usr/bin/env python3
"""THIRTEEN CLAUSES - terminal frontend.

    python3 play.py
    python3 play.py --seed 1234
    python3 play.py --load mysave
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from frontends.terminal.main import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv)
