from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services.image_service import is_blank_frame


def frame_with_bright_share(share: float, *, dark=(0, 0, 0)) -> Image.Image:
    """A 1000x100 frame whose left `share` is bright and the remainder `dark`."""
    image = Image.new("RGB", (1000, 100), dark)
    if share:
        image.paste((200, 180, 160), (0, 0, int(1000 * share), 100))
    return image


def png(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


@pytest.mark.parametrize("image,blank", [
    (frame_with_bright_share(0), True),
    (frame_with_bright_share(0, dark=(5, 5, 5)), True),
    (frame_with_bright_share(0.01), True),
    (frame_with_bright_share(0.10), False),
    (frame_with_bright_share(0.50), False),
    (frame_with_bright_share(0, dark=(20, 22, 30)), False),  # a dark cave is still a real scene
    (frame_with_bright_share(1), False),
])
def test_only_effectively_black_frames_are_blank(image, blank):
    assert is_blank_frame(image) is blank


def test_black_frame_is_rejected_before_the_model_runs(monkeypatch):
    with TestClient(app) as client:
        reached = []

        async def analyze(*args):
            reached.append(args)
            raise AssertionError("the model pipeline must not run for a blank frame")

        monkeypatch.setattr(app.state.pipeline, "analyze", analyze)
        response = client.post("/api/v1/analyze", files={"image": ("frame.png", png(frame_with_bright_share(0)), "image/png")})

    assert response.status_code == 422 and response.json()["detail"]["code"] == "BLANK_FRAME"
    assert reached == []
