"""A tiny local fake of the Gemini REST API so the REAL google-genai SDK code path can be tested offline."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeGemini:
    def __init__(self, text="Hello from fake Gemini", missing_models=(), bad_key=False, tool_call=None):
        self.text, self.missing, self.bad_key, self.tool_call = text, set(missing_models), bad_key, tool_call
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path})
                self._send(200, {"models": [
                    {"name": "models/gemini-9-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-9-flash-image", "supportedGenerationMethods": ["generateContent"]}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                model = self.path.split("/models/")[-1].split(":")[0]
                outer.requests.append({"method": "POST", "path": self.path, "model": model, "body": body})
                if outer.bad_key:
                    return self._send(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}})
                if model in outer.missing:
                    return self._send(404, {"error": {"code": 404, "message": f"models/{model} is not found for API version v1beta", "status": "NOT_FOUND"}})
                raw = json.dumps(body)
                if outer.tool_call and "tools" in body and "functionResponse" not in raw:
                    parts = [{"functionCall": {"name": outer.tool_call[0], "args": outer.tool_call[1]}}]
                else:
                    parts = [{"text": outer.text}]
                self._send(200, {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
                                 "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()

    def posts(self):
        return [r for r in self.requests if r["method"] == "POST"]
