# HK Weather & School WhatsApp Notification PoC

A proof of concept that monitors official Hong Kong severe-weather and school-suspension information, detects meaningful state changes, and sends WhatsApp alerts through CallMeBot.

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
         Message Formatter
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

## Events currently monitored

| Event | Normalized status | Notification level |
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

Health check for Cloud Run.

```bash
curl http://localhost:8080/health
```

### `POST /check`

Fetch all official sources, normalize events, compare state, and send notifications when applicable.

```bash
curl -X POST http://localhost:8080/check
```

### `POST /test-notification`

Test WhatsApp message formatting and CallMeBot delivery.

```bash
curl -X POST http://localhost:8080/test-notification
```

With `DRY_RUN=true`, the message is returned in the response but is not sent.

---

# Local development

## 1. Clone the repository

```bash
git clone https://github.com/chienchitung/hk-weather-whatsapp-poc.git
cd hk-weather-whatsapp-poc
```

## 2. Create a virtual environment

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure environment variables

Copy the example file:

```bash
cp .env.example .env
```

Safe development defaults:

```text
DRY_RUN=true
BOOTSTRAP_SILENT=true
RSS_MAX_AGE_HOURS=12
REQUEST_TIMEOUT_SECONDS=15
STATE_PATH=/tmp/hk-weather-whatsapp-state.json
PORT=8080
```

## 5. Start the API

```bash
python app.py
```

The local service runs at:

```text
http://localhost:8080
```

## 6. Test the service

```bash
curl http://localhost:8080/health
curl -X POST http://localhost:8080/check
curl -X POST http://localhost:8080/test-notification
```

---

# CallMeBot setup

A CallMeBot API key is required only when you want to send a real WhatsApp notification.

After CallMeBot activates your WhatsApp number, configure:

```text
CALLMEBOT_PHONE=+852XXXXXXXX
CALLMEBOT_API_KEY=your_api_key
DRY_RUN=false
```

Do **not** commit your real phone number or API key into GitHub.

The `.env` file is excluded by `.gitignore`.

For Google Cloud, store the credentials as Cloud Run environment variables or, preferably, Google Secret Manager secrets.

---

# Deploy to Google Cloud Run

Recommended region for this PoC:

```text
asia-east2 (Hong Kong)
```

## 1. Create a Google Cloud project

Create a project in Google Cloud Console and attach a billing account.

Example project name:

```text
hk-weather-whatsapp-poc
```

## 2. Open Google Cloud Shell

Confirm the selected project:

```bash
gcloud config get-value project
```

If necessary:

```bash
gcloud config set project YOUR_PROJECT_ID
```

## 3. Clone this repository

```bash
git clone https://github.com/chienchitung/hk-weather-whatsapp-poc.git
cd hk-weather-whatsapp-poc
```

## 4. Deploy safely in DRY RUN mode

```bash
gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated \
  --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true
```

During the first deployment, Google Cloud may ask to enable services such as:

- Cloud Run API
- Cloud Build API
- Artifact Registry API

Enable them when prompted.

## 5. Verify Cloud Run

After deployment, Google Cloud returns a service URL similar to:

```text
https://hk-weather-whatsapp-poc-xxxxx.asia-east2.run.app
```

Test:

```bash
curl https://YOUR_SERVICE_URL/health
curl -X POST https://YOUR_SERVICE_URL/check
```

Keep `DRY_RUN=true` until official-source parsing has been verified.

---

# Configure Cloud Scheduler

Cloud Run only executes when it receives a request. Cloud Scheduler is used to trigger monitoring automatically.

Recommended PoC schedule:

```text
Every 1–2 minutes
```

For every minute, use the cron expression:

```text
* * * * *
```

Configure the Scheduler job as:

```text
Name: hk-weather-check
Region: asia-east2
Target type: HTTP
Method: POST
URL: https://YOUR_SERVICE_URL/check
Time zone: Asia/Hong_Kong
```

Because the PoC deployment uses `--allow-unauthenticated`, Scheduler can call `/check` without authentication.

For a production implementation, protect the endpoint and use authenticated Scheduler requests.

---

# Enable real WhatsApp notifications

After Cloud Run + Scheduler are verified:

1. Obtain the CallMeBot API key.
2. Add `CALLMEBOT_PHONE`.
3. Add `CALLMEBOT_API_KEY`.
4. Change `DRY_RUN` from `true` to `false`.
5. Deploy a new Cloud Run revision.
6. Call `/test-notification` first.
7. Confirm the WhatsApp message arrives before relying on automated alerts.

---

# Testing

Run unit tests locally:

```bash
pytest -q
```

Tests currently cover:

- T3 / T8 / T10 parsing
- Amber / Red / Black Rainstorm parsing
- Notification level mapping
- HKO warning normalization
- Education Bureau suspension parsing
- School-scope extraction

GitHub Actions also runs the test suite when repository changes are pushed.

---

# Current PoC limitations

## Ephemeral Cloud Run state

The current version stores state in:

```text
/tmp/hk-weather-whatsapp-state.json
```

Cloud Run local storage is ephemeral. A new instance may not retain the previous state.

This is acceptable for a technical PoC, but not for reliable production deduplication.

## Warning-cleared transitions

v0.1 detects active warning states but does not yet fully model an explicit:

```text
ACTIVE → CLEARED
```

transition when a warning disappears from HKO's current-warning response.

## CallMeBot scope

CallMeBot is suitable for personal testing. It should not be treated as the notification infrastructure for large employee groups.

## Keyword-based parsing

Official wording can change. Event patterns should be validated during real severe-weather events.

---

# Recommended v0.2 architecture

```text
Cloud Scheduler
      ↓
Cloud Run
      ↓
Official Sources
      ↓
Event Normalizer
      ↓
Rule Engine
      ↓
Firestore
      │
      ├── durable state
      └── audit history
      ↓
Notification Adapter
      ├── CallMeBot (PoC)
      └── WhatsApp Business API (production)
```

Recommended next improvements:

1. Replace local JSON state with Firestore.
2. Add explicit warning cancellation / recovery notifications.
3. Add configurable company work-policy mapping such as `T8 → WFH / do not report`.
4. Add structured event and notification audit logs.
5. Add retry handling for failed notifications.
6. Add Chinese / English message templates.
7. Add WhatsApp Business Cloud API support for multiple recipients.

---

# Cost considerations

For a low-volume PoC, Cloud Run and Cloud Scheduler usage can often remain within Google Cloud free allowances, but a billing account is still normally required.

To reduce the risk of unexpected charges:

- Keep Cloud Run minimum instances at `0`.
- Keep maximum instances low for this PoC.
- Configure a Google Cloud Billing Budget & Alert.
- Review Cloud Build and Artifact Registry usage after deployments.

---

# Official references

- GovHK RSS directory: https://www.gov.hk/tc/about/rss.htm
- HKO Open Data API: https://data.weather.gov.hk/weatherAPI/opendata/weather.php
- EDB Latest News RSS: https://www.edb.gov.hk/tc/whats_new_rss.xml
- HKSAR Government Press Release RSS: https://www.info.gov.hk/gia/rss/general_zh.xml
- CallMeBot WhatsApp endpoint: https://api.callmebot.com/whatsapp.php
- Google Cloud Run: https://cloud.google.com/run
- Google Cloud Scheduler: https://cloud.google.com/scheduler

## Disclaimer

This repository is a technical proof of concept. It is not a substitute for official instructions from the HKSAR Government, Hong Kong Observatory, Education Bureau, or an employer during severe weather.