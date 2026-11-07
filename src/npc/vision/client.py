"""Loopback-only client for the separately running Skyrim CV service."""
from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
from npc.contracts.context import VisualContext

ALLOWED_IMAGE_TYPES = frozenset({"image/png", "image/jpeg"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_CV_RESPONSE_BYTES = 128 * 1024

class CVFailure(RuntimeError):
    def __init__(self, code: str): self.code = code; super().__init__(code)

def validate_cv_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.port == 0:
        raise ValueError("CV port must be 1..65535")
    if (parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("CV URL must be a plain loopback HTTP origin")
    return url.rstrip("/")

def _texts(items, field: str, limit: int) -> tuple[str, ...]:
    values = []
    for item in items if isinstance(items, list) else []:
        value = item.get(field) if isinstance(item, dict) else None
        if isinstance(value, str) and value.strip(): values.append(value.strip()[:512])
        if len(values) == limit: break
    return tuple(values)

def normalize_scene_response(payload: object) -> VisualContext:
    if not isinstance(payload, dict) or not isinstance(payload.get("scene_descriptor"), dict): raise CVFailure("cv_invalid_response")
    descriptor = payload["scene_descriptor"]; identity = descriptor.get("scene_id") or payload.get("request_id")
    if not isinstance(identity, str) or not identity.strip(): raise CVFailure("cv_invalid_response")
    environment = descriptor.get("environment"); description = environment.get("description") if isinstance(environment, dict) else None
    provenance = descriptor.get("provenance") if isinstance(descriptor.get("provenance"), dict) else {}
    captured = descriptor.get("captured_at")
    uncertainties = descriptor.get("uncertainties")
    if not isinstance(uncertainties, list):
        uncertainties = []
    try:
        captured_at = datetime.fromisoformat(captured.replace("Z", "+00:00")) if isinstance(captured, str) else datetime.now(timezone.utc)
        if captured_at.tzinfo is None: raise ValueError
        return VisualContext(id=f"cv-{identity}"[:128], captured_at=captured_at,
            environment=description.strip()[:512] if isinstance(description, str) and description.strip() else None,
            characters=_texts(descriptor.get("characters"), "description", 4), objects=_texts(descriptor.get("objects"), "description", 8),
            uncertainties=tuple(item.strip()[:512] for item in uncertainties[:4] if isinstance(item, str) and item.strip()),
            detector_model=provenance.get("detector_model") if isinstance(provenance.get("detector_model"), str) else None,
            vlm_model=provenance.get("vlm_model") if isinstance(provenance.get("vlm_model"), str) else None)
    except (TypeError, ValueError): raise CVFailure("cv_invalid_response") from None

class CVSceneClient:
    def __init__(self, base_url: str, timeout_s: float = 120, *, transport=None):
        self.base_url = validate_cv_url(base_url)
        if not 1 <= timeout_s <= 180: raise ValueError("CV timeout must be 1..180 seconds")
        self.timeout_s = timeout_s
        self.transport = transport
    async def analyze(self, image: bytes, content_type: str) -> VisualContext:
        if content_type not in ALLOWED_IMAGE_TYPES: raise CVFailure("unsupported_image")
        if not image or len(image) > 10 * 1024 * 1024: raise CVFailure("unsupported_image")
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False, transport=self.transport) as client:
                async with client.stream("POST", f"{self.base_url}/api/v1/analyze", files={"image": ("screenshot.png", image, content_type)}) as response:
                    if response.status_code >= 500: raise CVFailure("cv_unavailable")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_CV_RESPONSE_BYTES: raise CVFailure("cv_invalid_response")
                    if response.status_code == 422 and b'"BLANK_FRAME"' in raw: raise CVFailure("cv_blank_frame")
                    if response.status_code != 200: raise CVFailure("cv_invalid_response")
        except httpx.TimeoutException: raise CVFailure("cv_timeout") from None
        except httpx.HTTPError: raise CVFailure("cv_unavailable") from None
        import json
        try: return normalize_scene_response(json.loads(raw))
        except ValueError: raise CVFailure("cv_invalid_response") from None
