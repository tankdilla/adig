import os
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
PROJECT_ROOT = ROOT.parents[1]
SHARED_DIR = PROJECT_ROOT / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

WORKER_DIR = PROJECT_ROOT / "services" / "worker" / "app"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

TEST_DB_PATH = ROOT / "tests" / "test_app.db"

os.environ.setdefault("DATABASE_URL", f"sqlite+pysqlite:///{TEST_DB_PATH}")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("SESSION_COOKIE_NAME", "test_cookie")
os.environ.setdefault("ADMIN_TOKEN", "MaryAndDarrell2026.")


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "TEXT"
