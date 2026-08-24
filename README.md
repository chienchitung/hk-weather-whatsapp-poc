# HK Weather & School WhatsApp Notification PoC

A proof of concept that monitors official Hong Kong severe-weather and school-suspension information, detects meaningful state changes, and sends human-readable WhatsApp alerts through CallMeBot.

> This project is intended for personal / technical PoC use. For organization-wide employee notifications, use an enterprise messaging channel such as the official WhatsApp Business Cloud API.

## Why this project exists

Hong Kong does not use the same territory-wide government "stop work / stop school" mechanism as Taiwan. Weather warnings, school suspension notices, and employer work arrangements are separate concepts.

This PoC therefore separates three layers:

1. **Official weather status** — Hong Kong Observatory (HKO)
2. **Official school suspension** — Education Bureau (EDB)
3. **Work arrangement** — company policy mapped from official events

The current PoC focuses on layers 1 and 2, while keeping the architecture ready for configurable company work rules later.

## Architecture

```text
Cloud Scheduler
      │
      │ POST /check every 1–2 minutes
      ▼
Google Cloud Run
      │
      ├── HKO Warning Summary API
      ├── HKO Special Weather Tips API
      ├── Education Bureau RSS
      └── HKSAR Government RSS
      │
      ▼
Event Normalizer
      │
      ▼
State Comparison / Deduplication
      │
      ├── No change → no notification
      │
      └── State changed
              │
              ▼
     Human-readable Message Formatter
              │
              ▼
           CallMeBot
              │
              ▼
           WhatsApp
```

## Official data sources

| Source | Endpoint | Purpose |
|---|---|---|
| Hong Kong Observatory | Open Data `warnsum` API | Tropical cyclone and rainstorm warning states |
| Hong Kong Observatory | Open Data `swt` API | Pre-No. 8 / special weather tips |
| Education Bureau | Latest News RSS | Official class-suspension announcements |
| HKSAR Government | Press release RSS | Backup for Extreme Conditions and special announcements |

No API key is required for these Hong Kong government data sources.

## Source health verification

The service reports each source separately. This is important because `events: []` alone does **not** prove that all sources were reachable.

Use:

```bash
curl https://YOUR_SERVICE_URL/sources
```

A healthy response looks like:

```json
{
  "source_health": "ok",
  "sources": [
    {
      "name": "hko_warning_summary",
      "source": "香港天文台－警告摘要",
      "ok": true,
      "events_found": 0,
      "detail": "來源讀取與解析成功，目前沒有符合通知條件的事件"
    },
    {
      "name": "hko_special_weather_tips",
      "source": "香港天文台－特別天氣提示",
      "ok": true,
      "events_found": 0
    },
    {
      "name": "edb_latest_news",
      "source": "香港教育局－最新消息",
      "ok": true,
      "events_found": 0
    },
    {
      "name": "govhk_press_release",
      "source": "香港政府新聞公報",
      "ok": true,
      "events_found": 0
    }
  ],
  "errors": []
}
```

Interpretation:

- `source_health = ok` → all configured official sources were fetched and parsed successfully.
- `ok = true` + `events_found = 0` → source worked, but there is currently no matching alert.
- `ok = false` → that source failed to fetch or parse; `/check` will return `source_health = degraded`.
- `errors = []` → no source-level errors occurred.

`POST /check` also includes the same `sources` array, so each scheduled run can be audited.

## Events currently monitored

| Event | Internal status | Notification level |
|---|---|---|
| Tropical Cyclone Signal No. 1 | `T1` | INFO |
| Tropical Cyclone Signal No. 3 | `T3` | INFO |
| Pre-No. 8 indication | `PRE_T8` | PREPARE |
| Tropical Cyclone Signal No. 8 | `T8` | ACTION_REQUIRED |
| Tropical Cyclone Signal No. 9 | `T9` | ACTION_REQUIRED |
| Tropical Cyclone Signal No. 10 | `T10` | ACTION_REQUIRED |
| Amber Rainstorm | `AMBER_RAIN` | INFO |
| Red Rainstorm | `RED_RAIN` | PREPARE |
| Black Rainstorm | `BLACK_RAIN` | ACTION_REQUIRED |
| Extreme Conditions | `EXTREME_CONDITIONS` | ACTION_REQUIRED |
| EDB class suspension | `SUSPENDED` | ACTION_REQUIRED |

Internal codes are used only for logic. WhatsApp recipients receive natural-language labels instead of system codes.

## Human-readable WhatsApp messages

Example — Typhoon Signal No. 8:

```text
🔴 香港天氣警報｜八號烈風或暴風信號已生效

八號東南烈風或暴風信號
目前狀況：八號烈風或暴風信號
前一狀況：三號強風信號

📌 請避免不必要外出，留意交通及安全情況；工作安排請依公司內部惡劣天氣政策執行。

發布單位：香港天文台
更新時間：2026-08-24 16:10 HKT
官方公告：https://...
```

Example — school suspension:

```text
🎓 停課通知｜教育局最新安排

教育局宣布上午校及全日制學校停課
目前狀況：停課
適用範圍：上午校、全日制學校

📌 請家長及學生留意教育局的最新安排，並以官方公告為準。

發布單位：香港教育局
更新時間：2026-08-24 16:10 HKT
官方公告：https://...
```

## Notification behavior

The service only considers sending a notification when the normalized state changes:

```text
previous state != current state
```

Example:

```text
T3
 ↓
Pre-T8   → notify
 ↓
T8       → notify
 ↓
T8       → no duplicate notification
```

Two safety settings are enabled by default:

- `DRY_RUN=true` — process events but do not send WhatsApp messages.
- `BOOTSTRAP_SILENT=true` — the first run records the current state without sending historical / already-active alerts.

## Project structure

```text
.
├── app.py
├── Dockerfile
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── tests/
│   └── test_rules.py
└── .github/
    └── workflows/
        └── test.yml
```

## API endpoints

### `GET /health`

Cloud Run process health check.

```bash
curl http://localhost:8080/health
```

### `GET /sources`

Fetch and verify every configured official source without sending notifications.

```bash
curl http://localhost:8080/sources
```

### `POST /check`

Fetch all official sources, report source health, normalize events, compare state, and send notifications when applicable.

```bash
curl -X POST http://localhost:8080/check
```

### `POST /test-notification`

Test human-readable WhatsApp formatting and CallMeBot delivery.

```bash
curl -X POST http://localhost:8080/test-notification
```

With `DRY_RUN=true`, the message is returned in the response but is not sent.

---

# Local development

```bash
git clone https://github.com/chienchitung/hk-weather-whatsapp-poc.git
cd hk-weather-whatsapp-poc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Windows activation:

```bash
.venv\Scripts\activate
```

Safe environment defaults:

```text
DRY_RUN=true
BOOTSTRAP_SILENT=true
RSS_MAX_AGE_HOURS=12
REQUEST_TIMEOUT_SECONDS=15
STATE_PATH=/tmp/hk-weather-whatsapp-state.json
PORT=8080
```

---

# CallMeBot setup

A CallMeBot API key is required only when you want to send a real WhatsApp notification.

Configure:

```text
CALLMEBOT_PHONE=your_phone_number
CALLMEBOT_API_KEY=your_api_key
DRY_RUN=false
```

Do **not** commit your real phone number or API key into GitHub. For Google Cloud, prefer Secret Manager for credentials.

---

# Deploy to Google Cloud Run

Recommended region:

```text
asia-east2 (Hong Kong)
```

From Cloud Shell:

```bash
git clone https://github.com/chienchitung/hk-weather-whatsapp-poc.git
cd hk-weather-whatsapp-poc

gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated \
  --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true
```

After deployment:

```bash
curl https://YOUR_SERVICE_URL/health
curl https://YOUR_SERVICE_URL/sources
curl -X POST https://YOUR_SERVICE_URL/check
```

If updating an already deployed service after a GitHub change:

```bash
git pull

gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated
```

Existing Cloud Run environment variables remain associated with the service unless explicitly changed.

---

# Configure Cloud Scheduler

Recommended PoC schedule:

```text
Every 1–2 minutes
```

Every minute cron:

```text
* * * * *
```

Scheduler target:

```text
Name: hk-weather-check
Region: asia-east2
Target type: HTTP
Method: POST
URL: https://YOUR_SERVICE_URL/check
Time zone: Asia/Hong_Kong
```

For production, protect `/check` with authenticated Scheduler requests.

---

# Testing

```bash
pytest -q
```

Tests cover typhoon parsing, rainstorm parsing, notification levels, HKO warning normalization, Education Bureau suspension parsing, and school-scope extraction.

---

# Current PoC limitations

- State is currently stored at `/tmp/hk-weather-whatsapp-state.json`; Cloud Run local storage is ephemeral.
- Explicit `ACTIVE → CLEARED` recovery notifications are not yet fully modeled when warnings disappear from HKO's current-warning response.
- CallMeBot is suitable for personal / small PoC use, not large-scale employee distribution.
- Government wording can change, so keyword patterns should be validated during real severe-weather events.

## Recommended next improvements

1. Replace local state with Firestore.
2. Add explicit warning cancellation / recovery notifications.
3. Add configurable company work-policy mapping.
4. Add structured event and notification audit logs.
5. Add retry handling for failed notifications.
6. Add Google Sheets recipient management for non-technical administrators.
7. Add WhatsApp Business Cloud API support for multi-recipient production use.

## Disclaimer

This repository is a technical proof of concept. It is not a substitute for official instructions from the HKSAR Government, Hong Kong Observatory, Education Bureau, or an employer during severe weather.
