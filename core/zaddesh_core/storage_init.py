"""Initialize the selected storage backend for the dev stack and deployments."""
from pathlib import Path
import subprocess
import sys

from zaddesh_core.config import settings


def main():
    if settings.database.url.startswith(("mongodb://", "mongodb+srv://")):
        import zaddesh_core.models  # noqa: F401
        from zaddesh_core.mongo_store import ensure_schema
        ensure_schema()
        print("MongoDB collections and indexes are ready.")
    else:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=Path(__file__).resolve().parents[1], check=True)


if __name__ == "__main__":
    main()
