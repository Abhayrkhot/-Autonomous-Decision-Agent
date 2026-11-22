"""Test-only services for e2e/run.sh. Never used by the application itself.

webhook: HTTPS sink on :8443. POST /receive records a delivery; GET /received lists them.
model:   Responses API stub on :8080. POST /v1/responses returns a structured decision;
         GET /requests lists what it was sent. It applies the same request checks the
         application relies on (auth, store=false, strict json_schema, no tools).
"""

import argparse
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = "e2e-model-key"
STRICT_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items"}
RECORDS: list[dict] = []
LOCK = threading.Lock()


def keywords(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            children = value.values() if key == "properties" else [value]
            for child in children:
                yield from keywords(child)


def schema_problems(payload: dict) -> list[str]:
    problems = []
    fmt = payload.get("text", {}).get("format", {})
    schema = fmt.get("schema", {})
    if fmt.get("type") != "json_schema" or fmt.get("strict") is not True:
        problems.append("strict json_schema output required")
    if payload.get("store") is not False:
        problems.append("store must be false")
    if "tools" in payload:
        problems.append("tools must not be exposed")
    if set(keywords(schema)) - STRICT_KEYWORDS:
        problems.append("unsupported schema keyword")
    if schema.get("additionalProperties") is not False or set(
        schema.get("required", [])
    ) != set(schema.get("properties", {})):
        problems.append("strict schemas require every property and no extras")
    return problems


class Handler(BaseHTTPRequestHandler):
    mode = "webhook"

    def log_message(self, *args):
        pass

    def reply(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/received", "/requests"):
            with LOCK:
                return self.reply(200, list(RECORDS))
        if self.path == "/health":
            return self.reply(200, {"status": "ok"})
        self.reply(404, {"detail": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.mode == "webhook" and self.path == "/receive":
            with LOCK:
                RECORDS.append(
                    {
                        "idempotency_key": self.headers.get("Idempotency-Key"),
                        "body": body,
                    }
                )
            return self.reply(200, {"received": True})
        if self.mode == "model" and self.path == "/v1/responses":
            return self.model(body)
        self.reply(404, {"detail": "not found"})

    def model(self, payload: dict) -> None:
        authorized = self.headers.get("Authorization") == f"Bearer {API_KEY}"
        problems = schema_problems(payload)
        data = json.loads(payload.get("input", "{}"))
        evidence = [item["document_id"] for item in data.get("evidence", [])]
        failing = "stub-fail" in data.get("objective", "")
        with LOCK:
            RECORDS.append(
                {
                    "model": payload.get("model"),
                    "authorized": authorized,
                    "schema_problems": problems,
                    "evidence": evidence,
                    "forced_failure": failing,
                }
            )
        if not authorized:
            return self.reply(401, {"error": {"message": "invalid api key"}})
        if problems:
            return self.reply(400, {"error": {"message": "; ".join(problems)}})
        if failing:
            return self.reply(503, {"error": {"message": "overloaded"}})
        name = data.get("context", {}).get("name", "there")
        cited = evidence[:1]
        reference = f" (see {cited[0]})" if cited else ""
        decision = {
            "plan": ["Review the cited evidence", "Offer one concrete next step"],
            "action": "Offer a guided onboarding session",
            "message": f"Hi {name}, a short guided session can unblock setup{reference}.",
            "citations": cited,
        }
        text = json.dumps(decision)
        self.reply(
            200,
            {
                "id": f"resp_e2e_{len(RECORDS)}",
                "object": "response",
                "status": "completed",
                "model": payload.get("model"),
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
                "usage": {
                    "input_tokens": len(payload["input"]) // 4,
                    "output_tokens": len(text) // 4,
                },
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["webhook", "model"])
    parser.add_argument("--cert")
    parser.add_argument("--key")
    args = parser.parse_args()
    Handler.mode = args.mode
    port = 8443 if args.mode == "webhook" else 8080
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    if args.mode == "webhook":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
