from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Protocol


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list: ...

    def offsets(self, text: str) -> list[tuple[int, int]]: ...


class LocalEmbedder:
    name = "all-MiniLM-L6-v2-onnx-v1"

    def __init__(self, cache_dir: Path) -> None:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        import onnxruntime
        import warnings
        if hasattr(onnxruntime, "preload_dlls"):
            onnxruntime.preload_dlls()
        providers = onnxruntime.get_available_providers()
        preferred = ["CUDAExecutionProvider", "CPUExecutionProvider"] if "CUDAExecutionProvider" in providers else ["CPUExecutionProvider"]
        self.function = ONNXMiniLM_L6_V2(preferred_providers=preferred)
        if "CUDAExecutionProvider" not in preferred:
            warnings.warn("MiniLM embedding model is running on CPU (CUDA provider unavailable).", RuntimeWarning)
        # Keep model downloads inside this project rather than the user's home.
        self.function.DOWNLOAD_PATH = cache_dir / "all-MiniLM-L6-v2"

    @cached_property
    def tokenizer(self):
        from tokenizers import Tokenizer

        self._encode(["Initialize the local embedding model."])
        tokenizer = Tokenizer.from_str(self.function.tokenizer.to_str())
        tokenizer.no_padding()
        tokenizer.no_truncation()
        return tokenizer

    def offsets(self, text: str) -> list[tuple[int, int]]:
        return self.tokenizer.encode(text, add_special_tokens=False).offsets

    def embed(self, texts: list[str]) -> list:
        for text in texts:
            if len(self.offsets(text)) > 254:
                raise ValueError("Embedding input exceeds 254 tokens; shorten the query.")
        return self._encode(texts)

    def _encode(self, texts):
        import warnings
        try:
            result = self.function(texts)
        except Exception:
            if "CUDAExecutionProvider" not in (self.function._preferred_providers or []):
                raise
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
            cache = self.function.DOWNLOAD_PATH
            self.function = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])
            self.function.DOWNLOAD_PATH = cache
            warnings.warn("MiniLM embedding model is running on CPU (GPU execution failed).", RuntimeWarning)
            result = self.function(texts)
        if "CUDAExecutionProvider" not in self.function.model.get_providers():
            warnings.warn("MiniLM embedding model is running on CPU.", RuntimeWarning)
        return result


def split_text(text: str, embedder: Embedder, size: int, overlap: int) -> list[str]:
    """Use model token offsets to preserve source text without silent truncation."""
    offsets = embedder.offsets(text)
    chunks = []
    start = 0
    while start < len(offsets):
        end = min(start + size, len(offsets))
        chunk = text[offsets[start][0]:offsets[end - 1][1]].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(offsets):
            break
        start = end - overlap
    return chunks
