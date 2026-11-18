#!/usr/bin/env python3
"""Read recent journal issues for the authenticated SNMP extension."""
import base64
from datetime import datetime, timezone
import json
import subprocess

try:
    result = subprocess.run(["journalctl", "--system", "-p", "0..4", "-n", "30", "-o", "json", "--no-pager"],
                            capture_output=True, text=True, timeout=10, check=True)
except (OSError, subprocess.SubprocessError):
    print("status|unavailable|System journal read access is unavailable")
else:
    if "not seeing messages" in result.stderr.lower() or "permission" in result.stderr.lower():
        print("status|unavailable|System journal read access is unavailable")
    else:
        print("status|ready")
        for line in result.stdout.splitlines():
            try:
                record = json.loads(line)
                stamp = int(record["__REALTIME_TIMESTAMP"])
                at = datetime.fromtimestamp(stamp / 1_000_000, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
                key = base64.b64encode(("linux|" + record["__CURSOR"]).encode()).decode()
                level = {"0": "emergency", "1": "alert", "2": "critical", "3": "error", "4": "warning"}.get(str(record.get("PRIORITY")), "warning")
                message = str(record.get("SYSLOG_IDENTIFIER", record.get("_COMM", "System journal"))) + "\n" + str(record["MESSAGE"])
                encoded = base64.b64encode(message.encode()).decode()
                count = (len(encoded) + 383) // 384
                for part in range(count):
                    print(f"{key}|{at}|{level}|{part}|{count}|{encoded[part * 384:(part + 1) * 384]}")
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
