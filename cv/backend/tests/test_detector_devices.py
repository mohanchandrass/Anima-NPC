import sys
from types import SimpleNamespace
import pytest
from PIL import Image
from app.detection.yolo_detector import YoloDetector


def test_yolo_prefers_cuda_and_falls_back_with_warning(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)))
    detector = YoloDetector("test", .5, 640)
    assert detector._resolve_device() == "cuda"
    calls = []
    class Model:
        def to(self, device): assert device == "cpu"
        def predict(self, image, **kwargs):
            calls.append(kwargs["device"])
            if kwargs["device"] == "cuda": raise RuntimeError("out of memory")
            return [SimpleNamespace(names={}, boxes=[])]
    detector._model, detector._device = Model(), "cuda"
    with pytest.warns(RuntimeWarning, match="model is running on CPU"):
        result = detector.detect(Image.new("RGB", (2, 2)))
    assert result.device == "cpu" and calls == ["cuda", "cpu"]
