from __future__ import annotations
import base64, json, time
import asyncio
from io import BytesIO
import httpx
from PIL import Image
from app.schemas.scene_descriptor import Detection
from app.services.image_service import resize_for_model
from npc.vision.client import validate_cv_url


class VLMUnavailableError(RuntimeError): pass
class VLMValidationError(RuntimeError): pass

PROMPT = """Analyze this Skyrim gameplay screenshot. YOLO detections below are supporting evidence only and may be wrong. Return ONLY one JSON object with exactly this shape, replacing each <...> with what you see:
{"environment": {"description": "<one sentence describing the visible place>", "location_guess": "<kind of place, or null>", "visible_conditions": ["<lighting, weather or time of day>"]},
"characters": [{"description": "<appearance>", "identity": null, "position": "<where in the image>", "visible_attributes": ["<clothing or item>"], "confidence_or_certainty": "certain|likely|uncertain"}],
"objects": [{"label": "<short name>", "description": "<what it looks like>", "position": "<where in the image>"}],
"player_actions": ["<sentence>"], "spatial_relationships": ["<sentence>"], "events": ["<sentence>"], "uncertainties": ["<what cannot be determined>"]}
Report only visible facts; use null or empty arrays when unknown, and never copy a <...> placeholder. Never claim exact NPC identity, player intent, game state, or an action unless visually clear. Detections: """


class OllamaVLM:
    def __init__(self, host: str, model: str, timeout_seconds: float, *, transport=None):
        self.host, self.model, self.timeout_seconds = validate_cv_url(host), model, timeout_seconds
        self.transport = transport

    async def readiness(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False, transport=self.transport) as client:
                response = await client.get(f"{self.host}/api/tags")
                response.raise_for_status()
                names = {m.get("name") for m in response.json().get("models", [])}
            return (self.model in names, "model available" if self.model in names else f"model '{self.model}' is not pulled")
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            return False, f"Ollama unavailable: {exc.__class__.__name__}"

    async def analyze(self, image: Image.Image, detections: list[Detection]) -> tuple[dict, float]:
        buffer = BytesIO(); resize_for_model(image, 1280).save(buffer, format="JPEG", quality=85)
        evidence = [d.model_dump(mode="json") for d in detections[:100]]
        payload = {"model": self.model, "prompt": PROMPT + json.dumps(evidence), "images": [base64.b64encode(buffer.getvalue()).decode()], "stream": False, "format": "json", "options": {"temperature": 0, "num_predict": 1024, "num_ctx": 4096}}
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False, transport=self.transport) as client:
                    async with client.stream("POST", f"{self.host}/api/generate", json=payload) as response:
                        response.raise_for_status()
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 256 * 1024:
                                raise VLMValidationError("VLM response exceeds 256 KB.")
                        try:
                            content = json.loads(raw).get("response", "")
                        except (ValueError, AttributeError):
                            raise VLMValidationError("Invalid VLM response envelope.") from None
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise VLMUnavailableError("Ollama request timed out.") from exc
        except httpx.HTTPError as exc:
            raise VLMUnavailableError("Configured local VLM provider is unavailable.") from exc
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict): raise ValueError("not an object")
        except (ValueError, TypeError) as exc:
            raise VLMValidationError("VLM returned invalid JSON; no scene descriptor was fabricated.") from exc
        return parsed, (time.perf_counter() - started) * 1000
