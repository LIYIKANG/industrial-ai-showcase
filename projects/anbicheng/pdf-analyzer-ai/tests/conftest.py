"""
tests/conftest.py
=================
Common pytest fixtures. Ensures tests use an in-memory SQLite DB without
touching the real application database.
"""

import sys
from pathlib import Path

# Allow `import auth`, `import core`, `import scripts` from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
