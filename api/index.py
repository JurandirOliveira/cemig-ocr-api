import os
import sys
import tempfile
from pathlib import Path

TMP_ROOT = Path(tempfile.gettempdir())
PADDLE_CACHE = TMP_ROOT / "paddlex"
PADDLE_CACHE.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(PADDLE_CACHE))
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("TMPDIR", str(TMP_ROOT))
os.environ.setdefault("TEMP", str(TMP_ROOT))
os.environ.setdefault("TMP", str(TMP_ROOT))

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_main import app  # noqa: E402,F401
