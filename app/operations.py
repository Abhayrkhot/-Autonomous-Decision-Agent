"""Bounded local request controls and content-free operational measurements."""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from uuid import uuid4

from starlette.responses import JSONResponse

logger = logging.getLogger("agent.requests")


class OperationalMiddleware:
    def __init__(
        self, app, state: dict, body_limit: int = 262144, rate_limit: int = 1000
    ):
        if body_limit < 1 or rate_limit < 1:
            raise ValueError("Request limits must be positive")
        self.app, self.state = app, state
        self.body_limit, self.rate_limit = body_limit, rate_limit
        self.clients: OrderedDict[str, tuple[int, int]] = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        trace_id = str(uuid4())
        started = time.perf_counter()
        status = 500

        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append(
                    (b"x-request-id", trace_id.encode())
                )
            await send(message)

        try:
            address = scope.get("client") or ("unknown", 0)
            key = address[0]
            minute = int(time.monotonic() // 60)
            previous, count = self.clients.get(key, (minute, 0))
            count = count + 1 if previous == minute else 1
            self.clients[key] = (minute, count)
            self.clients.move_to_end(key)
            if len(self.clients) > 1024:
                self.clients.popitem(last=False)
            if count > self.rate_limit:
                return await JSONResponse(
                    {"detail": "Rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )(scope, receive, tracked_send)
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body.extend(message.get("body", b""))
                        if len(body) > self.body_limit:
                            return await JSONResponse(
                                {"detail": "Request body too large"}, status_code=413
                            )(scope, receive, tracked_send)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await JSONResponse(
                    {"detail": "Request body timeout"}, status_code=408
                )(scope, receive, tracked_send)
            delivered = False

            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            await self.app(scope, buffered_receive, tracked_send)
        finally:
            elapsed = time.perf_counter() - started
            self.state["requests"] += 1
            self.state["errors"] += int(status >= 500)
            self.state["duration_seconds_total"] += elapsed
            # Route template avoids logging user-supplied path/query/body/header data.
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.info(
                json.dumps(
                    {
                        "request_id": trace_id,
                        "route": route,
                        "status": status,
                        "duration_seconds": elapsed,
                    }
                )
            )
