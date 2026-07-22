import json
from pathlib import Path

from app.services.feedback_profile import FeedbackProfile


def _write_rating(folder: Path, name: str, label: str, event_kind: str, rating: str) -> None:
    (folder / f"{name}.json").write_text(
        json.dumps({"label": label, "event_kind": event_kind, "rating": rating}),
        encoding="utf-8",
    )


def test_good_feedback_lowers_threshold_after_two_ratings(tmp_path: Path) -> None:
    _write_rating(tmp_path, "one", "SINGLE KILL", "kill", "good")
    _write_rating(tmp_path, "two", "SINGLE KILL", "kill", "good")
    profile = FeedbackProfile(tmp_path)
    assert profile.threshold_adjustment("kill", "SINGLE KILL") < 0


def test_bad_feedback_raises_threshold_after_two_ratings(tmp_path: Path) -> None:
    _write_rating(tmp_path, "one", "DRAGON SECURED", "dragon", "bad")
    _write_rating(tmp_path, "two", "DRAGON SECURED", "dragon", "bad")
    profile = FeedbackProfile(tmp_path)
    assert profile.threshold_adjustment("dragon", "DRAGON SECURED") > 0
