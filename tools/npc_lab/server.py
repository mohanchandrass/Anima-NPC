"""Bounded loopback-only HTTP bridge for a single-user development console."""

import asyncio
import concurrent.futures
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import monotonic

from tools.npc_lab.service import CancelRequest, DisplayAck, LabStateEdit, LabService, LabTurn
from npc.vision import CVFailure, ALLOWED_IMAGE_TYPES
from tools.npc_lab.media import MediaFailure
from npc.state.social_store import StateConflict, StoreFailure

STATIC = Path(__file__).parent / "web"
ASSETS = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}
for character in ("mira", "elin", "daren", "iona"):
    ASSETS[f"/portraits/{character}.svg"] = (f"portraits/{character}.svg", "image/svg+xml")
    ASSETS[f"/scenes/{character}.svg"] = (f"scenes/{character}.svg", "image/svg+xml")
ASSETS["/stage.js"] = ("stage.js", "text/javascript")
ASSETS["/camera.mjs"] = ("camera.mjs", "text/javascript")
ASSETS["/facial.mjs"] = ("facial.mjs", "text/javascript")
for font in ("BarlowCondensed-Regular.ttf", "Cinzel.ttf"):
    ASSETS[f"/fonts/{font}"] = (f"fonts/{font}", "font/ttf")
for module in ("three.module.js", "three.core.js", "GLTFLoader.js", "BufferGeometryUtils.js", "SkeletonUtils.js"):
    ASSETS[f"/vendor/{module}"] = (f"vendor/{module}", "text/javascript")


class LabServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service: LabService, port=8765):
        self.service = service
        self.token = secrets.token_urlsafe(32)
        self.workers = threading.BoundedSemaphore(8)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="npc-lab-loop")
        try:
            super().__init__(("127.0.0.1", port), LabHandler)
        except BaseException:
            self.loop.close()
            raise
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.loop_thread.start()

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.workers.release()

    def call(self, coroutine, *, timeout_s=None):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            default_timeout = max(12, self.service.runner.limits.response_timeout_ms / 1000 + 2)
            return future.result(timeout=default_timeout if timeout_s is None else timeout_s)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError from None

    def server_close(self):
        super().server_close()
        if self.loop.is_running():
            async def stop_tasks():
                provider = self.service.runner.provider
                if hasattr(provider, "aclose"):
                    await provider.aclose()
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.wait(tasks, timeout=1)
            try:
                asyncio.run_coroutine_threadsafe(stop_tasks(), self.loop).result(timeout=2)
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.loop_thread.join(timeout=2)
                if not self.loop_thread.is_alive():
                    self.loop.close()


class LabHandler(BaseHTTPRequestHandler):
    server: LabServer

    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, *args):
        pass  # no user text, tokens or provider errors in access logs

    def send(self, status, body, content_type="application/json", extra_headers=None):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith(("text/", "application/json")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host") == self.server.origin.removeprefix("http://")

    def reject_post(self, status, error):
        # Closing a Windows socket with unread request data can reset the
        # connection before the client receives its error. Drain only a small,
        # declared body under the socket timeout; never trust an unbounded size.
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if 0 < length <= 65536 and not self.headers.get("Transfer-Encoding"):
                self.rfile.read(length)
        except (OSError, ValueError):
            pass
        self.send(status, {"error": error})

    def do_GET(self):
        if not self.valid_host():
            self.send(403, {"error": "invalid_host"})
        elif self.path == "/api/info":
            try:
                self.send(200, self.server.service.info())
            except StoreFailure:
                self.send(503, {"error": "state_unavailable"})
        elif self.path.startswith("/api/scenes/") and self.path.endswith(".jpg"):
            try:
                if self.server.service.media is None: raise MediaFailure("scene_unavailable")
                self.send(200, self.server.service.media.scene_bytes(self.path.split("/")[-1].removesuffix(".jpg")), "image/jpeg")
            except MediaFailure:
                self.send(404, {"error": "scene_unavailable"})
        elif self.path in {"/api/fonts/dialogue.ttf", "/api/fonts/interface.ttf"}:
            try:
                if self.server.service.media is None:
                    raise MediaFailure("font_unavailable")
                self.send(200, self.server.service.media.font_bytes(self.path.split("/")[-1].removesuffix(".ttf")), "font/ttf")
            except MediaFailure:
                self.send(404, {"error": "font_unavailable"})
        elif self.path == "/api/background/skyrim.png":
            try:
                if self.server.service.media is None:
                    raise MediaFailure("background_unavailable")
                self.send(200, self.server.service.media.background_bytes(), "image/png")
            except MediaFailure:
                self.send(404, {"error": "background_unavailable"})
        elif self.path.startswith("/api/models/") and self.path.endswith(".glb"):
            persona_id = self.path.removeprefix("/api/models/").removesuffix(".glb")
            media = self.server.service.media
            if persona_id not in self.server.service.profiles or media is None:
                self.send(404, {"error": "model_unavailable"})
                return
            try:
                self.send(200, media.model_bytes(persona_id), "model/gltf-binary")
            except (MediaFailure, OSError, ValueError):
                self.send(404, {"error": "model_unavailable"})
        elif self.path in ASSETS:
            name, mime = ASSETS[self.path]
            body = (STATIC / name).read_bytes()
            if name == "index.html":
                body = body.replace(b"__LAB_TOKEN__", self.server.token.encode("ascii"))
            self.send(200, body, mime)
        else:
            self.send(404, {"error": "not_found"})

    def do_POST(self):
        if not self.valid_host() or self.headers.get("Origin") != self.server.origin or not hmac.compare_digest(
            self.headers.get("X-Lab-Token", "").encode("utf-8"), self.server.token.encode("ascii")
        ):
            self.reject_post(403, "invalid_origin_or_token")
            return
        if self.path == "/api/vision/analyze":
            try:
                if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() not in ALLOWED_IMAGE_TYPES:
                    self.reject_post(415, "image_required")
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 10 * 1024 * 1024:
                    self.reject_post(413, "image_limit")
                    return
                image = self.rfile.read(length)
                if len(image) != length: raise ValueError("incomplete image")
                # CV can take longer than a dialogue turn while a local VLM is cold.
                cv_timeout = getattr(self.server.service.cv_client, "timeout_s", 30) + 5
                result = self.server.call(self.server.service.analyze_visual(image, self.headers.get_content_type()), timeout_s=cv_timeout)
                self.send(200, result)
            except CVFailure as error:
                self.send(503 if error.code in {"cv_disabled", "cv_unavailable", "cv_timeout"} else 422, {"error": error.code})
            except TimeoutError:
                self.send(503, {"error": "cv_timeout"})
            except ValueError:
                self.send(400, {"error": "invalid_image"})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                self.send(500, {"error": "lab_failure"})
            return
        if self.path not in {"/api/turn", "/api/cancel", "/api/speech", "/api/state", "/api/displayed"}:
            self.reject_post(404, "not_found")
            return
        try:
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                self.reject_post(415, "json_required")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 16384:
                self.reject_post(413, "body_limit")
                return
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete body")
            if self.path == "/api/turn":
                turn = LabTurn.model_validate_json(body)
                result = self.server.call(self.server.service.turn(turn))
            elif self.path == "/api/cancel":
                request = CancelRequest.model_validate_json(body)
                result = self.server.call(self.server.service.cancel(request))
            elif self.path == "/api/state":
                request = LabStateEdit.model_validate_json(body)
                result = self.server.call(self.server.service.edit_state(request))
            elif self.path == "/api/displayed":
                request = DisplayAck.model_validate_json(body)
                result = self.server.call(self.server.service.displayed(request))
            else:
                request = CancelRequest.model_validate_json(body)
                start = monotonic()
                raw = self.server.call(self.server.service.speech(request))
                self.send(200, raw, "audio/wav", {"X-Synthesis-Ms": round((monotonic() - start) * 1000)})
                return
            self.send(409 if result.get("error") == "lab_busy" else 200, result)
        except ValueError:
            self.send(400, {"error": "invalid_test_input"})
        except MediaFailure as error:
            self.send(409 if error.code == "voice_busy" else 503, {"error": error.code})
        except StateConflict:
            self.send(409, {"error": "state_conflict_reload"})
        except StoreFailure:
            self.send(503, {"error": "state_unavailable"})
        except TimeoutError:
            self.send(504, {"error": "lab_timeout"})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self.send(500, {"error": "lab_failure"})  # never raw validation/provider details
