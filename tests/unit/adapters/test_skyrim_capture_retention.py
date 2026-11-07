"""Real game frames are about 4 MB each, so retention must be bounded by bytes as well as by count."""
from npc.adapters.skyrim.assets import Artifacts

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FRAME = PNG_SIGNATURE + b"x" * 192  # 200 bytes
BUDGET = 2000


def artifacts(tmp_path, *, max_screenshots=100, frame=FRAME):
    return Artifacts(tmp_path, max_bytes=BUDGET, max_screenshots=max_screenshots, capture=lambda _: frame)


def frames(tmp_path):
    return list((tmp_path / "screenshots").glob("frame-*.png"))


def test_continuous_capture_cannot_exhaust_the_output_budget(tmp_path):
    store = artifacts(tmp_path)

    for index in range(50):
        store.screenshot(f"{index}", 1)

    assert sum(path.stat().st_size for path in frames(tmp_path)) <= BUDGET // 4
    store.event("after-capture", {"kind": "still-writable"})
    assert (tmp_path / "inbox" / "after-capture.json").exists()


def test_newest_frame_is_kept_and_oldest_are_evicted_first(tmp_path):
    store = artifacts(tmp_path)

    first = store.screenshot("first", 1)
    for index in range(20):
        latest = store.screenshot(f"{index}", 1)

    remaining = {str(path.resolve()) for path in frames(tmp_path)}
    assert latest in remaining and first not in remaining


def test_count_limit_still_applies_when_frames_are_small(tmp_path):
    store = artifacts(tmp_path, max_screenshots=2)

    for index in range(5):
        store.screenshot(f"{index}", 1)

    assert len(frames(tmp_path)) == 2


def test_single_frame_above_the_screenshot_share_is_still_saved(tmp_path):
    store = artifacts(tmp_path, frame=PNG_SIGNATURE + b"x" * 992)  # over BUDGET // 4, inside BUDGET

    for index in range(3):
        store.screenshot(f"{index}", 1)

    assert len(frames(tmp_path)) == 1
