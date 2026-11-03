"""PyInstaller entry point (an absolute import so the frozen app finds the package)."""

import sys

from omnimonitor.cli import main

if __name__ == "__main__":
    sys.exit(main())
