import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser
import requests
from fastapi import FastAPI, HTTPException

HKT = timezone(timedelta(hours=8))

HKO_WARNING_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=warnsum&lang=tc"
HKO_SWT_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=swt&lang=tc"
EDB_RSS_URL = "https://www.edb.gov.hk/tc/whats_new_rss.xml"
GOVHK_RSS_URL = "https://www.info.gov.hk/gia/rss/general_zh.xml"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"


@dataclass(frozen=True)
class Event:
    key: str
    event_type: str
    status: str
    level: str
    title: str
    source: str
    source_url: str
    published_at: str | None = None
    scope: str | None = None


class Settings:
    phone = os.getenv("CALLMEBOT_PHONE", "")
    api_key = os.getenv("CALLMEBOT_API_KEY", "")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    bootstrap_silent = os.getenv("BOOTSTRAP_SILENT", "true").lower() == "true"
    rss_max_age_hours = int(os.getenv("RSS_MAX_AGE_HOURS", "12"))
    state_path = Path(os.getenv("STATE_PATH", "/tmp/hk-weather-whatsapp-state.json"))
    request_timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))


settings = Settings()
app = FastAPI(title="HK Weather & School WhatsApp PoC", version="0.1.0")


def fetch_json(url: str) -> Any:
    r = requests.get(url, timeout=settings.request_timeout, headers={"User-Agent": "hk-weather-whatsapp-poc/0.1"})
    r.raise_for_status()
    return r.json()


def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=settings.request_timeout, headers={"User-Agent": "hk-weather-whatsapp-poc/0.1"})
    r.raise_for_status()
    return r.text


def detect_typhoon_status(text: str) -> str | None:
    checks = [
        (r"(?:十號|10號|No\.\s*10)", "T10"),
        (r"(?:九號|9號|No\.\s*9)", "T9"),
        (r"(?:八號|8號|No\.\s*8)", "T8"),
        (r"(?:三號|3號|No\.\s*3)", "T3"),
        (r"(?:一號|1號|No\.\s*1)", "T1"),
    ]
    for pattern, status in checks:
        if re.search(pattern, text, flags=re.I):
            return status
    return None


def detect_rain_status(text: str) -> str | None:
    if re.search(r"黑色.*暴雨|Black Rainstorm", text, flags=re.I):
        return "BLACK_RAIN"
    if re.search(r"紅色.*暴雨|Red Rainstorm", text, flags=re.I):
        return "RED_RAIN"
    if re.search(r"黃色.*暴雨|Amber Rainstorm", text, flags=re.I):
        return "AMBER_RAIN"
    return None


def level_for(event_type: str, status: str) -> str:
    if status in {"T8", "T9", "T10", "BLACK_RAIN", "EXTREME_CONDITIONS", "SUSPENDED"}:
        return "ACTION_REQUIRED"
    if status in {"PRE_T8", "RED_RAIN"}:
        return "PREPARE"
    return "INFO"


def normalize_hko_warning_summary(data: dict[str, Any]) -> list[Event]:
    events: list[Event] = []
    for raw in data.values():
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", ""))
        code = str(raw.get("code", ""))
        text = f"{name} {code}"

        typhoon = detect_typhoon_status(text)
        if typhoon:
            events.append(Event(
                key="weather:typhoon",
                event_type="TYPHOON",
                status=typhoon,
                level=level_for("TYPHOON", typhoon),
                title=name or f"熱帶氣旋警告 {typhoon}",
                source="香港天文台",
                source_url=HKO_WARNING_URL,
                published_at=raw.get("updateTime") or raw.get("issueTime"),
            ))
            continue

        rain = detect_rain_status(text)
        if rain:
            events.append(Event(
                key="weather:rainstorm",
                event_type="RAINSTORM",
                status=rain,
                level=level_for("RAINSTORM", rain),
                title=name or rain,
                source="香港天文台",
                source_url=HKO_WARNING_URL,
                published_at=raw.get("updateTime") or raw.get("issueTime"),
            ))
    return events


def normalize_special_weather_tips(data: Any) -> list[Event]:
    texts: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                texts.append(" ".join(str(v) for v in item.values() if isinstance(v, (str, int))))
            else:
                texts.append(str(item))
    elif isinstance(data, dict):
        texts.append(json.dumps(data, ensure_ascii=False))
    else:
        texts.append(str(data))

    combined = "\n".join(texts)
    pre_t8_patterns = [
        r"預計.*(?:八號|8號).*信號",
        r"考慮.*(?:八號|8號).*信號",
        r"No\.\s*8.*(?:expected|consider)",
    ]
    if any(re.search(p, combined, flags=re.I | re.S) for p in pre_t8_patterns):
        return [Event(
            key="weather:pre_t8",
            event_type="TYPHOON_PRE_ALERT",
            status="PRE_T8",
            level="PREPARE",
            title="香港天文台預告可能發出八號熱帶氣旋警告信號",
            source="香港天文台",
            source_url=HKO_SWT_URL,
        )]
    return []


def parse_entry_time(entry: Any) -> datetime | None:
    struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not struct:
        return None
    return datetime(*struct[:6], tzinfo=timezone.utc).astimezone(HKT)


def recent_entries(feed_text: str) -> list[Any]:
    parsed = feedparser.parse(feed_text)
    cutoff = datetime.now(HKT) - timedelta(hours=settings.rss_max_age_hours)
    output = []
    for entry in parsed.entries:
        published = parse_entry_time(entry)
        if published is None or published >= cutoff:
            output.append(entry)
    return output


def school_scope(text: str) -> str:
    scopes = []
    pairs = [
        ("上午校", r"上午校|AM schools?"),
        ("下午校", r"下午校|PM schools?"),
        ("全日制", r"全日制|whole-day schools?"),
        ("夜校", r"夜校|evening schools?"),
    ]
    for label, pattern in pairs:
        if re.search(pattern, text, flags=re.I):
            scopes.append(label)
    return "、".join(scopes) if scopes else "請參閱教育局公告"


def normalize_edb(feed_text: str) -> list[Event]:
    events = []
    for entry in recent_entries(feed_text):
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        combined = f"{title} {summary}"
        if re.search(r"停課|classes? (?:are )?suspended|suspension of classes", combined, flags=re.I):
            events.append(Event(
                key="school:suspension",
                event_type="SCHOOL_SUSPENSION",
                status="SUSPENDED",
                level="ACTION_REQUIRED",
                title=title or "教育局停課公告",
                source="香港教育局",
                source_url=getattr(entry, "link", EDB_RSS_URL),
                published_at=parse_entry_time(entry).isoformat() if parse_entry_time(entry) else None,
                scope=school_scope(combined),
            ))
            break
    return events


def normalize_govhk(feed_text: str) -> list[Event]:
    events = []
    for entry in recent_entries(feed_text):
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        combined = f"{title} {summary}"
        if re.search(r"極端情況|Extreme Conditions", combined, flags=re.I):
            events.append(Event(
                key="government:extreme_conditions",
                event_type="EXTREME_CONDITIONS",
                status="EXTREME_CONDITIONS",
                level="ACTION_REQUIRED",
                title=title,
                source="香港政府新聞公報",
                source_url=getattr(entry, "link", GOVHK_RSS_URL),
                published_at=parse_entry_time(entry).isoformat() if parse_entry_time(entry) else None,
            ))
            break
    return events


def collect_events() -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    errors: list[str] = []
    sources = [
        ("HKO warning summary", lambda: normalize_hko_warning_summary(fetch_json(HKO_WARNING_URL))),
        ("HKO special weather tips", lambda: normalize_special_weather_tips(fetch_json(HKO_SWT_URL))),
        ("EDB RSS", lambda: normalize_edb(fetch_text(EDB_RSS_URL))),
        ("GovHK RSS", lambda: normalize_govhk(fetch_text(GOVHK_RSS_URL))),
    ]
    for name, fn in sources:
        try:
            events.extend(fn())
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return events, errors


def load_state() -> dict[str, str]:
    if not settings.state_path.exists():
        return {}
    try:
        return json.loads(settings.state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict[str, str]) -> None:
    settings.state_path.parent.mkdir(parents=True, exist_ok=True)
    settings.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def format_message(event: Event, previous: str | None) -> str:
    icon = "🔴" if event.level == "ACTION_REQUIRED" else "⚠️" if event.level == "PREPARE" else "ℹ️"
    lines = [
        f"{icon} *香港惡劣天氣／停課通知*",
        "",
        event.title,
        f"狀態：{event.status}",
    ]
    if event.scope:
        lines.append(f"適用範圍：{event.scope}")
    if previous:
        lines.append(f"前一狀態：{previous}")
    lines.extend([
        f"資料來源：{event.source}",
        f"官方連結：{event.source_url}",
        f"更新時間：{datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}",
        "",
        "注意：工作安排仍應依公司內部政策執行。",
    ])
    return "\n".join(lines)


def send_whatsapp(message: str) -> dict[str, Any]:
    if settings.dry_run:
        return {"sent": False, "dry_run": True, "message": message}
    if not settings.phone or not settings.api_key:
        raise RuntimeError("CALLMEBOT_PHONE and CALLMEBOT_API_KEY are required when DRY_RUN=false")
    r = requests.get(
        CALLMEBOT_URL,
        params={"phone": settings.phone, "text": message, "apikey": settings.api_key},
        timeout=settings.request_timeout,
    )
    r.raise_for_status()
    return {"sent": True, "status_code": r.status_code, "response": r.text[:500]}


def process_once() -> dict[str, Any]:
    events, errors = collect_events()
    previous_state = load_state()
    first_run = not bool(previous_state)
    next_state = dict(previous_state)
    notifications = []

    for event in events:
        previous = previous_state.get(event.key)
        next_state[event.key] = event.status
        changed = previous != event.status
        if not changed:
            continue
        if first_run and settings.bootstrap_silent:
            notifications.append({"event": asdict(event), "action": "bootstrap_only"})
            continue
        result = send_whatsapp(format_message(event, previous))
        notifications.append({"event": asdict(event), "action": "notify", "result": result})

    save_state(next_state)
    return {
        "checked_at": datetime.now(HKT).isoformat(),
        "events": [asdict(e) for e in events],
        "notifications": notifications,
        "errors": errors,
        "dry_run": settings.dry_run,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/check")
def check() -> dict[str, Any]:
    return process_once()


@app.post("/test-notification")
def test_notification() -> dict[str, Any]:
    event = Event(
        key="test:notification",
        event_type="TEST",
        status="TEST",
        level="PREPARE",
        title="PoC 測試通知：香港惡劣天氣監控服務已連線",
        source="PoC",
        source_url="https://www.gov.hk/tc/about/rss.htm",
    )
    try:
        return send_whatsapp(format_message(event, None))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), reload=False)
