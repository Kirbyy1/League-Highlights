from app.services.smart_scoring import PlayCandidate, score_candidate


def test_routine_single_is_skipped_but_low_health_solo_is_kept() -> None:
    routine = score_candidate(PlayCandidate(kind="kill", kill_count=1), sensitivity="balanced")
    assert routine.score == 12
    assert not routine.keep

    good = score_candidate(
        PlayCandidate(kind="kill", kill_count=1, solo_kills=1, min_health_percent=8),
        sensitivity="balanced",
    )
    assert good.keep
    assert good.score >= 50


def test_triple_and_objective_steal_are_always_kept() -> None:
    triple = score_candidate(PlayCandidate(kind="kill", kill_count=3), sensitivity="strict")
    steal = score_candidate(PlayCandidate(kind="baron", stolen=True), sensitivity="strict")
    assert triple.keep
    assert steal.keep


def test_death_after_play_reduces_score() -> None:
    survived = score_candidate(PlayCandidate(kind="kill", kill_count=2), sensitivity="balanced")
    died = score_candidate(
        PlayCandidate(kind="kill", kill_count=2, died_after=True), sensitivity="balanced"
    )
    assert died.score < survived.score
