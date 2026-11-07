from __future__ import annotations
import time
from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageDraw
from app.schemas.scene_descriptor import Detection


class DetectorUnavailableError(RuntimeError):
    pass


@dataclass
class DetectionResult:
    detections: list[Detection]
    annotated_image: Image.Image
    duration_ms: float
    device: str


class NoDetector:
    """Explicit VLM-only mode; no detections are fabricated or weights loaded."""
    model_name = "disabled"

    def is_available(self):
        return True, "detector disabled; VLM-only mode"

    def detect(self, image):
        return DetectionResult([], image.copy(), 0, "none")


class YoloDetector:
    def __init__(self, model_name: str, confidence: float, image_size: int, device: str = "auto"):
        self.model_name, self.confidence, self.image_size, self.requested_device = model_name, confidence, image_size, device
        self._model = None
        self._device = None

    def _resolve_device(self) -> str:
        if self.requested_device != "auto":
            return self.requested_device
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from ultralytics import YOLO
            self._model = YOLO(self.model_name)
            self._device = self._resolve_device()
            if self._device == "cpu":
                import warnings
                warnings.warn(f"YOLO {self.model_name} model is running on CPU.", RuntimeWarning)
        except Exception as exc:
            raise DetectorUnavailableError(f"YOLO model '{self.model_name}' could not be loaded: {exc}") from exc

    def is_available(self) -> tuple[bool, str]:
        if self._model is not None:
            return True, self._device or "loaded"
        if Path(self.model_name).is_file():
            return True, "local weights found; model not loaded"
        return False, "model is not loaded and no local weight path was found (lazy loading prevents download on readiness checks)"

    def detect(self, image: Image.Image) -> DetectionResult:
        self._load()
        started = time.perf_counter()
        try:
            result = self._model.predict(image, conf=self.confidence, imgsz=self.image_size, device=self._device, verbose=False)[0]
            names = result.names
            detections = [Detection(class_id=int(box.cls[0]), label=str(names[int(box.cls[0])]), confidence=float(box.conf[0]), bbox_xyxy=tuple(float(v) for v in box.xyxy[0].tolist())) for box in result.boxes]
        except RuntimeError as exc:
            if self._device != "cpu":
                self._device = "cpu"
                self._model.to("cpu")
                import warnings
                warnings.warn(f"YOLO {self.model_name} model is running on CPU (GPU inference failed).", RuntimeWarning)
                return self.detect(image)
            raise DetectorUnavailableError(f"YOLO inference failed: {exc}") from exc
        except Exception as exc:
            raise DetectorUnavailableError(f"YOLO inference failed: {exc}") from exc
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        for item in detections:
            draw.rectangle(item.bbox_xyxy, outline=(0, 255, 90), width=max(2, image.width // 600))
            draw.text((item.bbox_xyxy[0], max(0, item.bbox_xyxy[1] - 14)), f"{item.label} {item.confidence:.2f}", fill=(0, 255, 90))
        return DetectionResult(detections, annotated, (time.perf_counter() - started) * 1000, self._device or "unknown")
