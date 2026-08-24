# HK Weather & School WhatsApp Notification PoC

A proof of concept that monitors official Hong Kong public information and sends WhatsApp alerts through CallMeBot when an actionable weather or school-suspension state changes.

> PoC status: designed for personal testing. For employee-wide or production notifications, replace CallMeBot with the official WhatsApp Business Cloud API or another enterprise notification channel.

## What this PoC monitors

| Source | Endpoint | Purpose |
|---|---|---|
| Hong Kong Observatory | Open Data `warnsum` API | Tropical cyclone and rainstorm warning states |
| Hong Kong Observatory | Open Data `swt` API | Pre-No. 8 / special weather tips |
| Education Bureau | Latest News RSS | Official class suspension announcements |
| HKSAR Government | Press release RSS | Backup layer for Extreme Conditions / special announcements |

## Architecture

```text
Official sources
      ↓
Collector / parser
      ↓
Normalized events
      ↓
State comparison
      ↓
Only notify on change
      ↓
CallMeBot
      ↓
WhatsApp
```

Hong Kong does not use the same territory-wide government stop-work mechanism as Taiwan. The PoC therefore separates official weather status, official school suspension, and company-specific work arrangements.

## Safety defaults

Two safeguards are enabled by default:

- `DRY_RUN=true`: no WhatsApp message is sent.
- `BOOTSTRAP_SILENT=true`: first run establishes current state without sending alerts.

## Project structure

```text
.
├── app.py
├── Dockerfile
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── tests/
    └── test_rules.py
```

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Then set environment variables:

```bash
export DRY_RUN=true
export BOOTSTRAP_SILENT=true
```

Start the API:

```bash
python app.py
```

## Endpoints

Health check:

```bash
curl http://localhost:8080/health
```

Check official sources once:

```bash
curl -X POST http://localhost:8080/check
```

Test message formatting / delivery:

```bash
curl -X POST http://localhost:8080/test-notification
```

With `DRY_RUN=true`, the test message is returned but not sent.

## CallMeBot setup

Set the following only after CallMeBot activates your WhatsApp number:

```bash
CALLMEBOT_PHONE=+852XXXXXXXX
CALLMEBOT_API_KEY=your_api_key
DRY_RUN=false
```

Credentials must not be committed to Git. `.env` is excluded by `.gitignore`.

## Event rules in v0.1

| Event | Status | Level |
|---|---|---|
| Pre-No. 8 indication | `PRE_T8` | PREPARE |
| Typhoon Signal No. 8 | `T8` | ACTION_REQUIRED |
| Typhoon Signal No. 9 | `T9` | ACTION_REQUIRED |
| Typhoon Signal No. 10 | `T10` | ACTION_REQUIRED |
| Red Rainstorm | `RED_RAIN` | PREPARE |
| Black Rainstorm | `BLACK_RAIN` | ACTION_REQUIRED |
| Extreme Conditions | `EXTREME_CONDITIONS` | ACTION_REQUIRED |
| EDB class suspension | `SUSPENDED` | ACTION_REQUIRED |

T1, T3 and Amber Rain are also parsed as informational states so later transitions can be detected.

## Deduplication

Notifications are only considered when:

```text
previous state != current state
```

Current PoC state is stored in a local JSON file. This is sufficient for demonstration but not durable across Cloud Run instance replacement.

## Docker

```bash
docker build -t hk-weather-whatsapp-poc .
docker run --rm -p 8080:8080 \
  -e DRY_RUN=true \
  -e BOOTSTRAP_SILENT=true \
  hk-weather-whatsapp-poc
```

## Deploy to Google Cloud Run

The recommended PoC architecture is:

```text
Cloud Scheduler
      ↓ POST /check every 1–2 min
Cloud Run
      ↓
HKO / EDB / GovHK
      ↓
Rule + state comparison
      ↓
CallMeBot
      ↓
WhatsApp
```

From the repository root:

```bash
gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated \
  --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true
```

After deployment, test:

```bash
curl https://YOUR_SERVICE_URL/health
curl -X POST https://YOUR_SERVICE_URL/check
```

Then configure Cloud Scheduler to send an HTTP `POST` request to:

```text
https://YOUR_SERVICE_URL/check
```

## Cloud Run state warning

The current PoC uses `/tmp/hk-weather-whatsapp-state.json`. Cloud Run filesystems are ephemeral and instances do not share local state. Before using this operationally, replace local state with Firestore or another durable shared store.

Recommended v0.2:

```text
Cloud Scheduler
      ↓
Cloud Run
      ↓
Firestore ← durable deduplication state
      ↓
CallMeBot / WhatsApp Business API
```

## Testing

```bash
pytest -q
```

Tests cover typhoon parsing, rainstorm parsing, notification levels, HKO normalization, and Education Bureau suspension parsing.

## Known PoC limitations

- CallMeBot is appropriate for personal PoC use, not mass employee messaging.
- Government wording can change, so keyword patterns should be validated during real severe-weather events.
- Pre-No. 8 wording may require additional patterns.
- v0.1 does not yet model an explicit `ACTIVE → CLEARED` event when a warning disappears entirely from HKO's current-warning response.
- Local JSON state is not production-safe on Cloud Run.

## Recommended v0.2

1. Replace local state with Firestore.
2. Add explicit `ACTIVE → CLEARED` transition detection.
3. Add configurable company policy mapping.
4. Add structured audit logs and retries.
5. Add Chinese + English message templates.
6. Add WhatsApp Business Cloud API adapter for multi-recipient use.

## Official references

- GovHK RSS directory: https://www.gov.hk/tc/about/rss.htm
- HKO Open Data API: https://data.weather.gov.hk/weatherAPI/opendata/weather.php
- EDB Latest News RSS: https://www.edb.gov.hk/tc/whats_new_rss.xml
- HKSAR Government Press Release RSS: https://www.info.gov.hk/gia/rss/general_zh.xml
- CallMeBot endpoint: https://api.callmebot.com/whatsapp.php

## Disclaimer

This is a technical proof of concept. It is not a substitute for official HKSAR Government, Hong Kong Observatory, Education Bureau, or employer instructions during severe weather.
