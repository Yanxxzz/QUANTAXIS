"""Absolute-path service entry point; independent of the OS startup cwd."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panda_alpha.team.service import main

if __name__ == "__main__":
    main()
