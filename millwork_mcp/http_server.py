# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""Hosted mode: millwork-mcp over streamable HTTP, for Microsoft Foundry agents and
other remote MCP clients.

Security model
--------------
* Every request to /mcp must carry the shared key, either as
  ``Authorization: Bearer <key>`` or ``x-api-key: <key>``. Keys are compared in constant
  time. In Foundry, store the key in a project connection (auth type ``custom-keys``),
  never in agent code.
* The server refuses to start without MILLWORK_API_KEY (at least 32 characters), so it
  can never be exposed unauthenticated by accident.
* Stateless: no session state is kept between calls (required by Container Apps and
  Foundry), and requests are capped in size.
* /healthz is the only unauthenticated route and returns no data.

Run:  MILLWORK_API_KEY=... python -m millwork_mcp.http_server   (listens on $PORT, default 8000)
"""
from __future__ import annotations

import hmac
import logging
import os
import sys

import uvicorn
from starlette.responses import JSONResponse, PlainTextResponse

from .server import NOTICE, mcp

log = logging.getLogger("millwork.http")
MIN_KEY_LEN = 32
MAX_BODY = 1_000_000  # 1 MB is far more than any cabinet schedule


class KeyAuth:
    """ASGI middleware: shared-key auth in front of the MCP endpoint."""

    def __init__(self, app, keys: list[str]):
        self.app = app
        self.keys = [k.encode() for k in keys]

    def _ok(self, presented: str | None) -> bool:
        if not presented:
            return False
        p = presented.encode()
        # check every key so timing doesn't reveal which one matched
        return any([hmac.compare_digest(p, k) for k in self.keys])

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/healthz":
            return await PlainTextResponse("ok")(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        try:
            if int(headers.get("content-length", "0")) > MAX_BODY:
                return await JSONResponse({"error": "request too large"}, 413)(scope, receive, send)
        except ValueError:
            return await JSONResponse({"error": "bad content-length"}, 400)(scope, receive, send)
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key")
        if not self._ok(token):
            log.warning("rejected request to %s from %s", path,
                        (scope.get("client") or ("?",))[0])
            return await JSONResponse({"error": "unauthorized"}, 401,
                                      headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        return await self.app(scope, receive, send)


def load_keys() -> list[str]:
    raw = os.environ.get("MILLWORK_API_KEY", "")
    # comma-separated so a key can be rotated without downtime (old,new)
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        sys.exit("MILLWORK_API_KEY is not set - refusing to start an unauthenticated server.")
    short = [k for k in keys if len(k) < MIN_KEY_LEN]
    if short:
        sys.exit(f"MILLWORK_API_KEY must be at least {MIN_KEY_LEN} characters.")
    return keys


def build_app(keys: list[str] | None = None):
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.streamable_http_path = "/mcp"
    # Host-header check: pin to the public hostname(s) when known (set after the first
    # deploy), otherwise rely on the key. Azure Container Apps terminates TLS in front.
    hosts = [h.strip() for h in os.environ.get("MILLWORK_ALLOWED_HOSTS", "").split(",") if h.strip()]
    ts = mcp.settings.transport_security
    if ts is not None:
        if hosts:
            ts.enable_dns_rebinding_protection = True
            ts.allowed_hosts = hosts + [f"{h}:*" for h in hosts]
            ts.allowed_origins = [f"https://{h}" for h in hosts]
        else:
            ts.enable_dns_rebinding_protection = False
    keys = keys if keys is not None else load_keys()
    mcp._session_manager = None   # fresh session manager per app instance
    return KeyAuth(mcp.streamable_http_app(), keys)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info(NOTICE)
    app = build_app()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*", log_level="info")


if __name__ == "__main__":
    main()
