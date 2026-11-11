"""Receive allowlisted UDP syslog and forward it to ZadDesh."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

SEVERITIES = ("emergency", "alert", "critical", "error", "warning", "notice", "info", "debug")
PRIORITY = re.compile(rb"^<([0-9]{1,3})>")
logger = logging.getLogger("zaddesh.syslog")


class Receiver(asyncio.DatagramProtocol):
    def __init__(self, queue, allowed):
        self.queue = queue
        self.allowed = allowed
        self.dropped = 0

    def datagram_received(self, data, addr):
        sender = ipaddress.ip_address(addr[0])
        if not any(sender in network for network in self.allowed):
            return
        priority = PRIORITY.match(data)
        if not priority or int(priority[1]) > 191:
            return
        message = data[priority.end():].decode("utf-8", errors="replace")[:8192].rstrip("\r\n\0")
        if not message:
            return
        entry = {
            "event_id": str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "source": str(sender),
            "severity": SEVERITIES[int(priority[1]) % 8],
            "message": message,
        }
        try:
            self.queue.put_nowait(entry)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 100 == 0:
                logger.warning("Syslog queue overflow, dropped %d datagrams", self.dropped)


async def forward(args, token):
    queue = asyncio.Queue(maxsize=4096)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: Receiver(queue, args.allow), local_addr=(args.bind, args.port),
    )
    logger.info("Receiving UDP syslog on %s:%s", args.bind, args.port)
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            while True:
                entries = [await queue.get()]
                await asyncio.sleep(0.2)
                while len(entries) < 20 and not queue.empty():
                    entries.append(queue.get_nowait())
                delay = 1
                while True:
                    try:
                        response = await client.post(
                            args.server.rstrip("/") + "/api/v1/logs/ingest",
                            headers={"Authorization": f"Bearer {token}"}, json={"entries": entries},
                        )
                        response.raise_for_status()
                        break
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status not in (408, 429) and status < 500:
                            raise RuntimeError(f"ZadDesh rejected the syslog batch with HTTP {status}") from None
                        logger.warning("Log storage returned HTTP %d. Retrying in %d seconds", status, delay)
                    except httpx.RequestError:
                        logger.warning("Could not reach the log endpoint. Retrying in %d seconds", delay)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
    finally:
        transport.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1", help="Address to listen on")
    parser.add_argument("--port", type=int, default=5514)
    parser.add_argument("--allow", action="append", type=ipaddress.ip_network, required=True, help="Allowed source CIDR")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="ZadDesh base URL")
    args = parser.parse_args()
    token = os.environ.get("ZADDESH_LOGS__INGEST_TOKEN")
    if not token or len(token) < 32:
        parser.error("Set ZADDESH_LOGS__INGEST_TOKEN in the environment.")
    server = urlsplit(args.server)
    if not server.hostname or server.username or server.password or not (
        server.scheme == "https" or (server.scheme == "http" and server.hostname in {"127.0.0.1", "::1", "localhost"})
    ):
        parser.error("Use HTTPS unless forwarding to a local ZadDesh instance.")
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(forward(args, token))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
