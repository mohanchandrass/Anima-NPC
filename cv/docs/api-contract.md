# API contract

- `GET /health` returns `{"status":"ok"}` without contacting models.
- `GET /ready` checks lazy YOLO availability and Ollama/model availability. It does not run inference.
- `POST /api/v1/analyze` accepts multipart field `image` (`image/png` or `image/jpeg`), maximum 10 MiB and 8192px per side by default. It returns raw detections, a validated descriptor, timing, and a relative annotated artifact URL.
- `GET /api/v1/artifacts/{uuid}.jpg` returns one generated annotation; arbitrary filesystem paths cannot be addressed.

Failures use FastAPI HTTP errors with `detail.code`: `INVALID_IMAGE` (422), `BLANK_FRAME` (422, at least 98% of pixels are effectively black, so the model is not called), `DETECTOR_UNAVAILABLE` (503), `VLM_UNAVAILABLE` (503), or `VLM_INVALID_OUTPUT` (503).
