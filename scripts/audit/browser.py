"""Run browser acceptance tests against an isolated SQL or MongoDB core."""
import argparse
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--db", type=Path)
parser.add_argument("--mongo", action="store_true", help="Use an empty validation database selected by ZADDESH_MONGO_TEST_URI")
parser.add_argument("--port", type=int, default=18767)
parser.add_argument("--capture", type=Path)
parser.add_argument("--grep", help="Playwright test name pattern")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
if not args.mongo and args.db is None:
    parser.error("Select --db or --mongo")
db = args.db.resolve() if args.db else None
if db is not None and db.exists():
    parser.error("Choose a new disposable database path")
env = os.environ.copy()
if args.mongo:
    uri = env.get("ZADDESH_MONGO_TEST_URI", "")
    if "validation" not in uri:
        parser.error("MongoDB browser runs require a dedicated validation database")
    env["ZADDESH_DATABASE__URL"] = uri
    env["ZADDESH_LOGS__MONGO_URI"] = uri
    from pymongo import MongoClient
    database = MongoClient(uri).get_default_database()
    if database.users.count_documents({}):
        parser.error("Choose an empty MongoDB validation database")
    env["ZADDESH_LOGS__DATABASE"] = database.name
else:
    env["ZADDESH_DATABASE__URL"] = "sqlite:///" + db.as_posix()
env["ZADDESH_E2E_URL"] = f"http://127.0.0.1:{args.port}"
env["ZADDESH_E2E_COLLECTOR"] = env["ZADDESH_COLLECTOR__SHARED_SECRET"] = secrets.token_hex(32)
env["ZADDESH_E2E_ENROLLMENT"] = env["ZADDESH_COLLECTOR__ENROLLMENT_TOKEN"] = secrets.token_hex(32)
env["ZADDESH_SECURITY__ENCRYPTION_KEY"] = secrets.token_hex(32)
env["ZADDESH_DEMO_MODE"] = "false"
if args.capture:
    env["ZADDESH_CAPTURE_DIR"] = str(args.capture.resolve())
subprocess.run([sys.executable, "-m", "zaddesh_core.storage_init"], env=env, cwd=root / "core", check=True)
startup = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
server = subprocess.Popen([
    sys.executable, "-m", "uvicorn", "zaddesh_core.main:app", "--host", "127.0.0.1",
    "--port", str(args.port), "--no-proxy-headers", "--no-access-log",
], cwd=root, env=env, **startup)
try:
    deadline = time.monotonic() + 30
    with httpx.Client(trust_env=False, timeout=1) as client:
        while True:
            try:
                client.get(env["ZADDESH_E2E_URL"] + "/readyz").raise_for_status()
                break
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if time.monotonic() > deadline or server.poll() is not None:
                    raise
                time.sleep(.2)
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if not npm:
        parser.error("npm was not found")
    command = [npm, "run", "test:e2e", "--", "--reporter=list"]
    if args.grep:
        command.extend(["--grep", args.grep])
    subprocess.run(command, cwd=root / "web", env=env, check=True)
finally:
    server.terminate()
    server.wait(timeout=15)

