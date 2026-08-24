from app import Event, NOTIFY_STATUSES, process_once, settings


def test_informational_states_not_in_notification_whitelist():
    assert "T1" not in NOTIFY_STATUSES
    assert "T3" not in NOTIFY_STATUSES
    assert "AMBER_RAIN" not in NOTIFY_STATUSES
    assert "T8" in NOTIFY_STATUSES
    assert "BLACK_RAIN" in NOTIFY_STATUSES
    assert "SUSPENDED" in NOTIFY_STATUSES


def test_quiet_bootstrap_does_not_suppress_first_real_event(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(settings, "state_backend", "local")
    monkeypatch.setattr(settings, "state_path", state_path)
    monkeypatch.setattr(settings, "dry_run", True)

    monkeypatch.setattr("app.collect_events", lambda: ([], [], []))
    first = process_once()
    assert first["decision"] == "bootstrap_completed_no_notification"
    assert first["notifications"] == []

    event = Event(
        key="weather:typhoon",
        event_type="TYPHOON",
        status="T8",
        level="ACTION_REQUIRED",
        title="八號烈風或暴風信號",
        source="香港天文台",
        source_url="https://example.com",
    )
    monkeypatch.setattr("app.collect_events", lambda: ([event], [], []))
    second = process_once()

    assert second["decision"] == "completed"
    assert second["notifications"][0]["action"] == "dry_run_preview"
    assert second["notifications"][0]["event"]["status"] == "T8"


def test_source_error_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "app.collect_events",
        lambda: ([], [], ["香港天文台－警告摘要: timeout"]),
    )
    result = process_once()
    assert result["decision"] == "fail_closed_source_error"
    assert result["notifications"] == []
