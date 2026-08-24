import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import feedparser
import requests
from fastapi import FastAPI, HTTPException
from google.cloud import firestore

HKT = timezone(timedelta(hours=8))

HKO_WARNING_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=warnsum&lang=tc"
HKO_SWT_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=swt&lang=tc"
EDB_RSS_URL = "https://www.edb.gov.hk/tc/whats_new_rss.xml"
GOVHK_RSS_URL = "https://www.info.gov.hk/gia/rss/general_zh.xml"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
META_KEY = "__meta__"

# Only actionable states are allowed to trigger WhatsApp. T1, T3 and Amber Rain
# remain useful context and are stored, but never notify by themselves.
NOTIFY_STATUSES = {
    "PRE_T8",
    "T8",
    "T9",
    "T10",
    "RED_RAIN",
    "BLACK_RAIN",
    "EXTREME_CONDITIONS",
    "SUSPENDED",
}


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


@dataclass(frozen=True)
class SourceCheck:
    name: str
    source: str
    url: str
    ok: bool
    events_found: int
    checked_at: str
    detail: str


class Settings:
    phone = os.getenv("CALLMEBOT_PHONE", "")
    api_key = os.getenv("CALLMEBOT_API_KEY", "")
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    bootstrap_silent = os.getenv("BOOTSTRAP_SILENT", "true").lower() == "true"
    rss_max_age_hours = int(os.getenv("RSS_MAX_AGE_HOURS", "12"))
    request_timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))
    state_backend = os.getenv(
        "STATE_BACKEND",
        "firestore" if os.getenv("K_SERVICE") else "local",
    ).lower()
    state_path = Path(os.getenv("STATE_PATH", "/tmp/hk-weather-whatsapp-state.json"))
    firestore_collection = os.getenv("FIRESTORE_COLLECTION", "hk_weather_notification_state")


settings = Settings()
app = FastAPI(title="HK Weather & School WhatsApp PoC", version="0.3.1")


def now_hkt() -> str:
    return datetime.now(HKT).isoformat()


def fetch_json(url: str) -> Any:
    r = requests.get(url, timeout=settings.request_timeout, headers={"User-Agent": "hk-weather-whatsapp-poc/0.3"})
    r.raise_for_status()
    return r.json()


def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=settings.request_timeout, headers={"User-Agent": "hk-weather-whatsapp-poc/0.3"})
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
    patterns = [
        r"預計.*(?:八號|8號).*信號",
        r"考慮.*(?:八號|8號).*信號",
        r"No\.\s*8.*(?:expected|consider)",
    ]
    if any(re.search(p, combined, flags=re.I | re.S) for p in patterns):
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
    if getattr(parsed, "bozo", False) and not parsed.entries:
        raise ValueError(f"RSS parse failed: {getattr(parsed, 'bozo_exception', 'unknown error')}")
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
        ("全日制學校", r"全日制|whole-day schools?"),
        ("夜校", r"夜校|evening schools?"),
    ]
    for label, pattern in pairs:
        if re.search(pattern, text, flags=re.I):
            scopes.append(label)
    return "、".join(scopes) if scopes else "請參閱教育局公告"


def normalize_edb(feed_text: str) -> list[Event]:
    for entry in recent_entries(feed_text):
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        combined = f"{title} {summary}"
        if re.search(r"停課|classes? (?:are )?suspended|suspension of classes", combined, flags=re.I):
            return [Event(
                key="school:suspension",
                event_type="SCHOOL_SUSPENSION",
                status="SUSPENDED",
                level="ACTION_REQUIRED",
                title=title or "教育局公布停課安排",
                source="香港教育局",
                source_url=getattr(entry, "link", EDB_RSS_URL),
                published_at=parse_entry_time(entry).isoformat() if parse_entry_time(entry) else None,
                scope=school_scope(combined),
            )]
    return []


def normalize_govhk(feed_text: str) -> list[Event]:
    for entry in recent_entries(feed_text):
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        combined = f"{title} {summary}"
        if re.search(r"極端情況|Extreme Conditions", combined, flags=re.I):
            return [Event(
                key="government:extreme_conditions",
                event_type="EXTREME_CONDITIONS",
                status="EXTREME_CONDITIONS",
                level="ACTION_REQUIRED",
                title=title or "政府公布極端情況安排",
                source="香港政府新聞公報",
                source_url=getattr(entry, "link", GOVHK_RSS_URL),
                published_at=parse_entry_time(entry).isoformat() if parse_entry_time(entry) else None,
            )]
    return []


def run_source_check(name: str, source: str, url: str, fn: Callable[[], list[Event]]) -> tuple[list[Event], SourceCheck]:
    checked_at = now_hkt()
    try:
        source_events = fn()
        return source_events, SourceCheck(
            name=name,
            source=source,
            url=url,
            ok=True,
            events_found=len(source_events),
            checked_at=checked_at,
            detail="來源讀取與解析成功" if source_events else "來源讀取與解析成功，目前沒有符合通知條件的事件",
        )
    except Exception as exc:
        return [], SourceCheck(
            name=name,
            source=source,
            url=url,
            ok=False,
            events_found=0,
            checked_at=checked_at,
            detail=f"{type(exc).__name__}: {exc}",
        )


def collect_events() -> tuple[list[Event], list[SourceCheck], list[str]]:
    definitions = [
        ("hko_warning_summary", "香港天文台－警告摘要", HKO_WARNING_URL, lambda: normalize_hko_warning_summary(fetch_json(HKO_WARNING_URL))),
        ("hko_special_weather_tips", "香港天文台－特別天氣提示", HKO_SWT_URL, lambda: normalize_special_weather_tips(fetch_json(HKO_SWT_URL))),
        ("edb_latest_news", "香港教育局－最新消息", EDB_RSS_URL, lambda: normalize_edb(fetch_text(EDB_RSS_URL))),
        ("govhk_press_release", "香港政府新聞公報", GOVHK_RSS_URL, lambda: normalize_govhk(fetch_text(GOVHK_RSS_URL))),
    ]
    events: list[Event] = []
    checks: list[SourceCheck] = []
    for name, source, url, fn in definitions:
        source_events, check = run_source_check(name, source, url, fn)
        events.extend(source_events)
        checks.append(check)
    errors = [f"{c.source}: {c.detail}" for c in checks if not c.ok]
    return events, checks, errors


class LocalStateStore:
    def load(self) -> dict[str, dict[str, Any]]:
        if not settings.state_path.exists():
            return {}
        try:
            data = json.loads(settings.state_path.read_text(encoding="utf-8"))
            return {
                key: (value if isinstance(value, dict) else {"current_status": value, "last_notified_status": None})
                for key, value in data.items()
            }
        except Exception:
            return {}

    def save_record(self, key: str, record: dict[str, Any]) -> None:
        state = self.load()
        state[key] = record
        settings.state_path.parent.mkdir(parents=True, exist_ok=True)
        settings.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


class FirestoreStateStore:
    def __init__(self) -> None:
        self.client = firestore.Client()
        self.collection = self.client.collection(settings.firestore_collection)

    def load(self) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for doc in self.collection.stream():
            data = doc.to_dict() or {}
            original_key = data.get("event_key") or doc.id.replace("__", ":")
            state[original_key] = data
        return state

    def save_record(self, key: str, record: dict[str, Any]) -> None:
        doc_id = "meta" if key == META_KEY else key.replace(":", "__")
        self.collection.document(doc_id).set(record, merge=True)


def get_state_store() -> LocalStateStore | FirestoreStateStore:
    if settings.state_backend == "firestore":
        return FirestoreStateStore()
    if settings.state_backend == "local":
        return LocalStateStore()
    raise RuntimeError(f"Unsupported STATE_BACKEND: {settings.state_backend}")


def human_status(status: str | None) -> str | None:
    labels = {
        "T1": "一號戒備信號",
        "T3": "三號強風信號",
        "T8": "八號烈風或暴風信號",
        "T9": "九號烈風或暴風風力增強信號",
        "T10": "十號颶風信號",
        "PRE_T8": "天文台預告可能發出八號信號",
        "AMBER_RAIN": "黃色暴雨警告",
        "RED_RAIN": "紅色暴雨警告",
        "BLACK_RAIN": "黑色暴雨警告",
        "EXTREME_CONDITIONS": "極端情況",
        "SUSPENDED": "停課",
        "TEST": "測試",
    }
    return labels.get(status, status) if status else None


def message_heading(event: Event) -> tuple[str, str]:
    if event.event_type == "SCHOOL_SUSPENSION":
        return "🎓", "停課通知｜教育局最新安排"
    if event.status in {"T8", "T9", "T10"}:
        return "🔴", f"香港天氣警報｜{human_status(event.status)}已生效"
    if event.status == "BLACK_RAIN":
        return "🔴", "香港天氣警報｜黑色暴雨警告已生效"
    if event.status == "RED_RAIN":
        return "⚠️", "香港天氣提醒｜紅色暴雨警告已生效"
    if event.status == "PRE_T8":
        return "⚠️", "香港天氣提醒｜天文台預告可能發出八號信號"
    if event.status == "EXTREME_CONDITIONS":
        return "🔴", "政府特別通知｜極端情況安排"
    if event.event_type == "TEST":
        return "✅", "通知測試成功"
    return "ℹ️", f"香港天氣更新｜{human_status(event.status)}"


def action_text(event: Event) -> str:
    if event.event_type == "SCHOOL_SUSPENSION":
        return "請家長及學生留意教育局的最新安排，並以官方公告為準。"
    if event.status in {"T8", "T9", "T10", "BLACK_RAIN"}:
        return "請避免不必要外出，留意交通及安全情況；工作安排請依公司內部惡劣天氣政策執行。"
    if event.status in {"PRE_T8", "RED_RAIN"}:
        return "請提前留意交通、天氣及公司工作安排，並持續查看官方最新消息。"
    if event.status == "EXTREME_CONDITIONS":
        return "請留意政府最新安排，並依公司內部政策執行。"
    return "請留意官方最新消息。"


def format_message(event: Event, previous: str | None) -> str:
    icon, heading = message_heading(event)
    lines = [f"{icon} *{heading}*", "", event.title, f"目前狀況：{human_status(event.status)}"]
    if event.scope:
        lines.append(f"適用範圍：{event.scope}")
    if previous:
        lines.append(f"前一狀況：{human_status(previous)}")
    lines.extend([
        "",
        f"📌 {action_text(event)}",
        "",
        f"發布單位：{event.source}",
        f"更新時間：{datetime.now(HKT).strftime('%Y-%m-%d %H:%M HKT')}",
        f"官方公告：{event.source_url}",
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


def fail_closed_payload(source_checks: list[SourceCheck], events: list[Event], errors: list[str], decision: str) -> dict[str, Any]:
    return {
        "checked_at": now_hkt(),
        "source_health": "ok" if not errors else "degraded",
        "sources": [asdict(c) for c in source_checks],
        "events": [asdict(e) for e in events],
        "notifications": [],
        "errors": errors,
        "dry_run": settings.dry_run,
        "state_backend": settings.state_backend,
        "decision": decision,
    }


def process_once() -> dict[str, Any]:
    events, source_checks, errors = collect_events()

    # Safety rule 1: partial official data must never trigger a notification.
    if errors:
        return fail_closed_payload(source_checks, events, errors, "fail_closed_source_error")

    try:
        store = get_state_store()
        previous_state = store.load()
    except Exception as exc:
        return fail_closed_payload(
            source_checks,
            events,
            [f"State store unavailable: {type(exc).__name__}: {exc}"],
            "fail_closed_state_error",
        )

    # Persist a global bootstrap marker. This is important when deployment occurs
    # on a quiet day with zero active events: a later first real T8 must notify.
    bootstrap_complete = bool(previous_state.get(META_KEY, {}).get("bootstrap_complete"))
    if not bootstrap_complete:
        try:
            for event in events:
                store.save_record(event.key, {
                    "event_key": event.key,
                    "current_status": event.status,
                    "last_notified_status": None,
                    "source": event.source,
                    "updated_at": now_hkt(),
                })
            store.save_record(META_KEY, {
                "event_key": META_KEY,
                "bootstrap_complete": True,
                "completed_at": now_hkt(),
            })
        except Exception as exc:
            return fail_closed_payload(
                source_checks,
                events,
                [f"Bootstrap state save failed: {type(exc).__name__}: {exc}"],
                "fail_closed_bootstrap_error",
            )
        return {
            "checked_at": now_hkt(),
            "source_health": "ok",
            "sources": [asdict(c) for c in source_checks],
            "events": [asdict(e) for e in events],
            "notifications": [],
            "errors": [],
            "dry_run": settings.dry_run,
            "state_backend": settings.state_backend,
            "decision": "bootstrap_completed_no_notification",
        }

    notifications = []
    for event in events:
        previous_record = previous_state.get(event.key, {})
        previous_status = previous_record.get("current_status")
        last_notified_status = previous_record.get("last_notified_status")
        changed = previous_status != event.status
        should_notify = (
            changed
            and event.status in NOTIFY_STATUSES
            and last_notified_status != event.status
        )

        record = {
            "event_key": event.key,
            "current_status": event.status,
            "last_notified_status": last_notified_status,
            "source": event.source,
            "updated_at": now_hkt(),
        }

        action = "no_change"
        result: dict[str, Any] | None = None
        if should_notify:
            try:
                result = send_whatsapp(format_message(event, previous_status))
                if result.get("sent") is True:
                    record["last_notified_status"] = event.status
                action = "notify" if result.get("sent") else "dry_run_preview"
            except Exception as exc:
                action = "notification_failed"
                result = {"sent": False, "error": f"{type(exc).__name__}: {exc}"}
        elif event.status not in NOTIFY_STATUSES:
            action = "informational_state_only"
        elif last_notified_status == event.status:
            action = "already_notified"

        try:
            store.save_record(event.key, record)
        except Exception as exc:
            return fail_closed_payload(
                source_checks,
                events,
                [f"State save failed: {type(exc).__name__}: {exc}"],
                "state_write_failed",
            )

        if action != "no_change":
            notifications.append({
                "event": asdict(event),
                "action": action,
                "previous_status": previous_status,
                "result": result,
            })

    return {
        "checked_at": now_hkt(),
        "source_health": "ok",
        "sources": [asdict(c) for c in source_checks],
        "events": [asdict(e) for e in events],
        "notifications": notifications,
        "errors": [],
        "dry_run": settings.dry_run,
        "state_backend": settings.state_backend,
        "decision": "completed",
    }


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "HK Weather & School WhatsApp Notification PoC",
        "version": "0.3.1",
        "docs": "/docs",
        "health": "/health",
        "sources": "/sources",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "0.3.1",
        "state_backend": settings.state_backend,
    }


@app.get("/sources")
def sources() -> dict[str, Any]:
    _, checks, errors = collect_events()
    return {
        "source_health": "ok" if not errors else "degraded",
        "sources": [asdict(c) for c in checks],
        "errors": errors,
    }


@app.post("/check")
def check() -> dict[str, Any]:
    return process_once()


@app.post("/test-notification")
def test_notification() -> dict[str, Any]:
    event = Event(
        key="test:notification",
        event_type="TEST",
        status="TEST",
        level="INFO",
        title="香港惡劣天氣監控服務已成功連線。",
        source="系統測試",
        source_url="https://www.gov.hk/tc/about/rss.htm",
    )
    try:
        return send_whatsapp(format_message(event, None))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), reload=False)
