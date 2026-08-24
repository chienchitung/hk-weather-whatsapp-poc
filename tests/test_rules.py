from app import detect_rain_status, detect_typhoon_status, level_for, normalize_edb, normalize_hko_warning_summary


def test_detect_typhoon_status():
    assert detect_typhoon_status("八號烈風或暴風信號") == "T8"
    assert detect_typhoon_status("No. 10 Hurricane Signal") == "T10"
    assert detect_typhoon_status("三號強風信號") == "T3"


def test_detect_rain_status():
    assert detect_rain_status("黑色暴雨警告信號") == "BLACK_RAIN"
    assert detect_rain_status("Red Rainstorm Warning Signal") == "RED_RAIN"
    assert detect_rain_status("黃色暴雨警告信號") == "AMBER_RAIN"


def test_level_mapping():
    assert level_for("TYPHOON", "T8") == "ACTION_REQUIRED"
    assert level_for("RAINSTORM", "RED_RAIN") == "PREPARE"
    assert level_for("TYPHOON", "T3") == "INFO"


def test_normalize_hko_warning_summary():
    fixture = {
        "WTCSGNL": {
            "name": "八號東南烈風或暴風信號",
            "code": "TC8SE",
            "actionCode": "ISSUE",
            "issueTime": "2026-08-24T10:00:00+08:00",
        },
        "WRAIN": {
            "name": "紅色暴雨警告信號",
            "code": "WRAINR",
            "actionCode": "ISSUE",
            "issueTime": "2026-08-24T10:05:00+08:00",
        },
    }
    events = normalize_hko_warning_summary(fixture)
    by_key = {e.key: e for e in events}
    assert by_key["weather:typhoon"].status == "T8"
    assert by_key["weather:rainstorm"].status == "RED_RAIN"


def test_normalize_edb_school_suspension():
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>EDB</title>
      <item>
        <title>教育局宣布上午校及全日制學校停課</title>
        <description>因應惡劣天氣，上午校及全日制學校今日停課。</description>
        <link>https://example.com/edb</link>
      </item>
    </channel></rss>"""
    events = normalize_edb(rss)
    assert len(events) == 1
    assert events[0].status == "SUSPENDED"
    assert "上午校" in events[0].scope
    assert "全日制" in events[0].scope
