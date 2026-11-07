from types import SimpleNamespace
import pytest
from npc.rag.backend.embeddings import LocalEmbedder


def test_embedding_prefers_cuda_and_preserves_cache(tmp_path, monkeypatch):
    calls = []
    class Function:
        def __init__(self, preferred_providers):
            self._preferred_providers = preferred_providers
            calls.append(self)
        def __call__(self, texts):
            if "CUDAExecutionProvider" in self._preferred_providers:
                raise RuntimeError("GPU out of memory")
            return [[1.0]]
        @property
        def model(self): return SimpleNamespace(get_providers=lambda: self._preferred_providers)
    monkeypatch.setattr("chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2", Function)
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setattr("onnxruntime.preload_dlls", lambda: None, raising=False)
    embedder = LocalEmbedder(tmp_path)
    assert calls[0]._preferred_providers == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    with pytest.warns(RuntimeWarning, match="running on CPU"):
        assert embedder._encode(["hello"]) == [[1.0]]
    assert calls[-1].DOWNLOAD_PATH == tmp_path / "all-MiniLM-L6-v2"
    assert calls[-1]._preferred_providers == ["CPUExecutionProvider"]
