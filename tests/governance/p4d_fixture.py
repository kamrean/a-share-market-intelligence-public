"""Historical data view for unchanged P4D assertions, never an approval or archive loader."""

import atexit
import importlib.util
from pathlib import Path
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[2]
_temporary = None


def view():
    global _temporary
    if _temporary is not None:
        return Path(_temporary.name)
    spec = importlib.util.spec_from_file_location("_p4d_fixture_current_governance",
                                                 REPO_ROOT / "tools/governance_p4t.py")
    current = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(current)
    _retained, historical = current.historical_contents(REPO_ROOT)
    temporary = tempfile.TemporaryDirectory(prefix="p4d-historical-data-")
    root = Path(temporary.name)
    try:
        for name, text in historical.items():
            # Archived Python is hash evidence only. Executable fixtures always
            # use the actual current implementation, including its narrow helpers.
            if name.endswith(".py"):
                text = current.g.read_text(REPO_ROOT, name)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    except BaseException:
        temporary.cleanup()
        raise
    _temporary = temporary
    atexit.register(temporary.cleanup)
    return root
