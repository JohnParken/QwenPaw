"""P0 tests never load the desktop test suite's provider/config fixtures."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
