from app.services.video_recorder import RecorderDiagnostics


def test_diagnostics_health_waiting():
    assert RecorderDiagnostics().health == "Waiting"


def test_diagnostics_health_good():
    item = RecorderDiagnostics(fps=60.0, drop_rate=0.1, updated_at=1.0)
    assert item.health == "Good"


def test_diagnostics_health_warning():
    item = RecorderDiagnostics(fps=58.0, drop_rate=0.8, updated_at=1.0)
    assert item.health == "Warning"


def test_diagnostics_health_poor():
    item = RecorderDiagnostics(fps=40.0, drop_rate=3.0, updated_at=1.0)
    assert item.health == "Poor"
