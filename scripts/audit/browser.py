"""Run browser acceptance tests with an isolated core and disposable SQLite DB."""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--db", required=True, type=Path)
parser.add_argument("--port", type=int, default=18767)
parser.add_argument("--capture", type=Path)
parser.add_argument("--grep", help="Playwright test name pattern")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
db = args.db.resolve()
if db.exists():
    parser.error("Choose a new disposable database path")
env = os.environ.copy()
env["ZADDESH_DATABASE__URL"] = "sqlite:///" + db.as_posix()
env["ZADDESH_E2E_URL"] = f"http://127.0.0.1:{args.port}"
env["ZADDESH_E2E_COLLECTOR"] = env["ZADDESH_COLLECTOR__SHARED_SECRET"] = secrets.token_hex(32)
env["ZADDESH_SECURITY__ENCRYPTION_KEY"] = secrets.token_hex(32)
env["ZADDESH_DEMO_MODE"] = "false"
if args.capture:
    env["ZADDESH_CAPTURE_DIR"] = str(args.capture.resolve())
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, cwd=root / "core", check=True)
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

