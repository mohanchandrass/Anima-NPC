"""Start-up banner: one line per component with its state, so a missing piece is visible at a glance."""

from pathlib import Path

import httpx

from .diagnostics import event as log_event


def cv_state(cv_url, *, transport=None, timeout_s: float = 1.5) -> str:
    """Probe the loopback CV service's /ready once. Never raises, never downloads or runs inference."""
    if not cv_url:
        return "disabled: cv_url not set"
    try:
        with httpx.Client(timeout=timeout_s, trust_env=False, transport=transport) as client:
            response = client.get(cv_url.rstrip("/") + "/ready")
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return f"unreachable: {cv_url}"
    vlm = body.get("vlm") if isinstance(body, dict) and isinstance(body.get("vlm"), dict) else {}
    detail = str(vlm.get("detail") or "model")[:80]
    return f"ready: {detail}" if vlm.get("available") else f"service up, model not ready: {detail}"


def banner_lines(settings, *, provider=("unknown", "unknown"), stt: str = "", tts: str = "", cv: str = "") -> list[tuple[str, str]]:
    kind, model = provider
    profile = Path(settings.provider_profile).name
    return [
        ("LLM", f"profile={profile} transport={kind} model={model}"),
        ("STT", stt or ("disabled" if not settings.stt_url else "external " + settings.stt_url)),
        ("TTS", tts or ("disabled (subtitles only)" if not settings.tts_url else "external " + settings.tts_url)),
        ("RAG", f"configured: {Path(settings.rag_config).name}" if settings.rag_config else "not configured"),
        ("MEMORY", f"scope={settings.memory_scope} db={'on' if settings.state_db else 'off'}"),
        ("CV", cv or cv_state(settings.cv_url) + (" (descriptor attached to prompts)" if settings.cv_url and settings.attach_visual_reference
                                                  else " (descriptor kept as reference only)" if settings.cv_url else "")),
        ("SCREENSHOTS", f"capture {'on' if settings.capture_screenshots else 'off'} max={settings.max_screenshots}"),
    ]


def log_banner(settings, **kwargs):
    for component, state in banner_lines(settings, **kwargs):
        log_event("BANNER", component=component, state=state)
