import json

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
        source_id="hko_warning_summary",
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


def test_one_source_error_does_not_block_other_valid_event(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "__meta__": {
                    "event_key": "__meta__",
                    "bootstrap_complete": True,
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(settings, "state_backend", "local")
    monkeypatch.setattr(settings, "state_path", state_path)
    monkeypatch.setattr(settings, "dry_run", True)

    event = Event(
        key="weather:typhoon",
        source_id="hko_warning_summary",
        event_type="TYPHOON",
        status="T8",
        level="ACTION_REQUIRED",
        title="八號烈風或暴風信號",
        source="香港天文台",
        source_url="https://example.com",
    )

    monkeypatch.setattr(
        "app.collect_events",
        lambda: (
            [event],
            [],
            ["香港教育局－最新消息: timeout"],
        ),
    )

    result = process_once()

    assert result["decision"] == "completed_with_source_or_state_warnings"
    assert len(result["notifications"]) == 1
    assert result["notifications"][0]["action"] == "dry_run_preview"
    assert result["notifications"][0]["event"]["status"] == "T8"
    assert result["errors"] == ["香港教育局－最新消息: timeout"]
