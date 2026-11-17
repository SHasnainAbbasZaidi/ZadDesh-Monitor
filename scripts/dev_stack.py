"""Run the implemented ZadDesh services together on loopback."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--collector", required=True, type=Path, help="Path to the built Rust collector executable")
parser.add_argument("--buffer", type=Path, default=Path(".audit/stack-spool"))
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--restart-collector", action="store_true", help="Keep the dashboard online and retry a collector that exits")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
binary = args.collector.resolve(strict=True)
if not (root / "web" / "dist" / "index.html").is_file():
    parser.error("Build the UI first: cd web && npm ci && npm run build")
if not os.getenv("ZADDESH_COLLECTOR_TOKEN"):
    parser.error("Set ZADDESH_COLLECTOR_TOKEN; see collector enrollment in README.md")
base_url = f"http://127.0.0.1:{args.port}"
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root / "core", check=True)
children = []
try:
    core = subprocess.Popen([sys.executable, "-m", "uvicorn", "zaddesh_core.main:app", "--host",
                             "127.0.0.1", "--port", str(args.port), "--no-proxy-headers"], cwd=root / "core")
    children.append(core)
    deadline = time.monotonic() + 25
    with httpx.Client(trust_env=False, timeout=2) as client:
        while True:
            if core.poll() is not None or time.monotonic() >= deadline:
                raise SystemExit("Core did not become ready")
            try:
                if client.get(base_url + "/readyz").is_success:
                    break
            except httpx.TransportError:
                pass
            time.sleep(.25)
    args.buffer.resolve().mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    collector_command = [str(binary), "--server-url", base_url, "--buffer-dir", str(args.buffer.resolve())]
    collector = subprocess.Popen(collector_command, cwd=root, creationflags=flags)
    children.append(collector)
    print(f"Core, UI and collector started. Open {base_url}", flush=True)
    while core.poll() is None:
        exit_code = collector.poll()
        if exit_code is None:
            time.sleep(.5)
            continue
        if not args.restart_collector:
            raise SystemExit("Collector exited; shutting down the stack")
        print(f"Collector exited ({exit_code}). Retrying in 5 seconds while the dashboard stays online.", flush=True)
        time.sleep(5)
        if core.poll() is None:
            try:
                collector = subprocess.Popen(collector_command, cwd=root, creationflags=flags)
            except OSError as error:
                print(f"Collector launch failed: {error}", file=sys.stderr, flush=True)
            else:
                children[-1] = collector
    raise SystemExit("Core exited; shutting down the stack")
except KeyboardInterrupt:
    pass
finally:
    for child in reversed(children):
        if child.poll() is None:
            try:
                child.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" and child is not core else
                                  signal.SIGTERM)
                child.wait(timeout=12)
            except (OSError, subprocess.TimeoutExpired):
                child.kill()
                child.wait()
