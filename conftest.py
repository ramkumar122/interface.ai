"""Pytest bootstrap.

Ensures the repository root is importable so tests can `import db...` and
`import reset_db` regardless of where pytest is invoked from.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
