from __future__ import annotations
from pathlib import Path
import time
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from app.config import settings
from app.detection.yolo_detector import YoloDetector, DetectorUnavailableError
from app.perception.pipeline import PerceptionPipeline, PipelineError
from app.services.image_service import BlankFrameError, ImageValidationError, is_blank_frame, validate_image
from app.vision.ollama_vlm import OllamaVLM

router = APIRouter()

def pipeline(request: Request) -> PerceptionPipeline: return request.app.state.pipeline

@router.get("/health")
async def health(): return {"status": "ok"}

@router.get("/ready")
async def ready(request: Request):
    p = pipeline(request); yolov, yolodetail = p.detector.is_available(); vlmv, vlmdetail = await p.vlm.readiness()
    return {"ready": yolov and vlmv, "detector": {"available": yolov, "detail": yolodetail}, "vlm": {"available": vlmv, "detail": vlmdetail}}

@router.post("/api/v1/analyze")
async def analyze(request: Request, image: UploadFile = File(...)):
    request_id = "pending"
    try:
        preprocessing_start = time.perf_counter()
        data = await image.read(settings.max_upload_bytes + 1)
        validated = validate_image(data, image.content_type, settings.max_upload_bytes, settings.max_image_dimension)
        if is_blank_frame(validated.image):
            raise BlankFrameError("Frame is effectively black; no scene was analyzed.")
        result = await pipeline(request).analyze(validated, (time.perf_counter() - preprocessing_start) * 1000)
        return result
    except BlankFrameError as exc:
        raise HTTPException(422, detail={"code": "BLANK_FRAME", "message": str(exc)}) from exc
    except ImageValidationError as exc:
        raise HTTPException(422, detail={"code": "INVALID_IMAGE", "message": str(exc)}) from exc
    except DetectorUnavailableError as exc:
        raise HTTPException(503, detail={"code": "DETECTOR_UNAVAILABLE", "message": str(exc)}) from exc
    except PipelineError as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": exc.message}) from exc

@router.get("/api/v1/artifacts/{artifact_name}")
async def artifact(artifact_name: str):
    # The route parameter intentionally accepts a basename only.
    if Path(artifact_name).name != artifact_name or not artifact_name.endswith(".jpg"):
        raise HTTPException(404, "Artifact not found")
    path = settings.artifact_dir / artifact_name
    if not path.is_file(): raise HTTPException(404, "Artifact not found")
    return FileResponse(path, media_type="image/jpeg")
