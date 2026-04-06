import sys
from pathlib import Path

# Make sure the src/ directory is on the path for all tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
