from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any

import httpx
from fastapi import HTTPException

MAX_RESPONSE = 2 * 1024 * 1024


def normalize_url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value.strip())
    except (httpx.InvalidURL, ValueError):
        raise HTTPException(422, "Enter a valid Zabbix HTTPS URL") from None
    if url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment:
        raise HTTPException(422, "Use a Zabbix HTTPS URL without credentials, query parameters, or a fragment")
    path = url.path.rstrip("/")
    if not path.endswith("/api_jsonrpc.php"):
        path += "/api_jsonrpc.php"
    return url.copy_with(path=path)


def pinned_target(url: httpx.URL) -> httpx.URL:
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(
            url.raw_host.decode("ascii"), url.port or 443, type=socket.SOCK_STREAM,
        )}
    except (OSError, ValueError):
        raise HTTPException(502, "The Zabbix hostname could not be resolved") from None
    for address in addresses:
        actual = getattr(address, "ipv4_mapped", None) or address
        if (actual.is_loopback or actual.is_link_local or actual.is_unspecified or actual.is_multicast
                or actual.is_reserved or str(actual) == "168.63.129.16"):
            raise HTTPException(422, "The Zabbix address is in a blocked network range")
    if not addresses:
        raise HTTPException(502, "The Zabbix hostname returned no usable address")
    address = min(addresses, key=lambda item: (item.version, int(item)))
    return url.copy_with(host=str(address))


class ZabbixAPI:
    def __init__(self, client: httpx.Client, url: httpx.URL, token: str, version: str = "") -> None:
        self.client = client
        self.url = url
        self.target = pinned_target(url)
        self.token = token
        self.version = version

    def call(self, method: str, params: dict[str, Any] | list[Any]) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        headers = {"Host": self.url.netloc.decode("ascii")}
        if method != "apiinfo.version":
            major, minor = (int(part) for part in self.version.split(".")[:2])
            if (major, minor) >= (6, 4):
                headers["Authorization"] = f"Bearer {self.token}"
            else:
                payload["auth"] = self.token
        try:
            with self.client.stream("POST", self.target, json=payload, headers=headers,
                                    extensions={"sni_hostname": self.url.raw_host.decode("ascii")}) as response:
                if response.status_code in (401, 403):
                    raise HTTPException(502, "Zabbix rejected the connection credentials")
                if not response.is_success:
                    raise HTTPException(502, f"Zabbix returned HTTP {response.status_code}")
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE:
                        raise HTTPException(502, "Zabbix returned too much data")
            result = json.loads(content)
        except httpx.TimeoutException:
            raise HTTPException(504, "The Zabbix API request timed out") from None
        except httpx.HTTPError:
            raise HTTPException(502, "Could not establish a verified HTTPS connection to Zabbix") from None
        except (UnicodeError, ValueError):
            raise HTTPException(502, "Zabbix did not return a valid JSON-RPC response") from None
        if not isinstance(result, dict) or result.get("jsonrpc") != "2.0" or result.get("id") != 1:
            raise HTTPException(502, "Zabbix did not return a valid JSON-RPC response")
        if "error" in result:
            raise HTTPException(502, f"Zabbix refused {method}. Check the token and the account’s API permissions.")
        if "result" not in result:
            raise HTTPException(502, "The Zabbix response is missing its result")
        return result["result"]

    def detect_version(self) -> str:
        version = self.call("apiinfo.version", [])
        if not isinstance(version, str) or not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,5}(?:[A-Za-z0-9.-]{0,20})", version):
            raise HTTPException(502, "Zabbix returned an unrecognized API version")
        self.version = version
        return version

    def rows(self, method: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        result = self.call(method, params)
        if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
            raise HTTPException(502, f"Zabbix returned an invalid {method} result")
        return result
