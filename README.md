# HK Weather & School WhatsApp Notification PoC

A proof of concept that monitors official Hong Kong severe-weather and school-suspension information and sends human-readable WhatsApp alerts through CallMeBot only when a meaningful new event is detected.

> Traditional Chinese guide: [README.zh-TW.md](README.zh-TW.md)

## What this service monitors

| Source | Purpose |
|---|---|
| Hong Kong Observatory `warnsum` API | Tropical cyclone and rainstorm warning states |
| Hong Kong Observatory `swt` API | Pre-No. 8 / special weather tips |
| Education Bureau RSS | Official school-suspension notices |
| HKSAR Government RSS | Extreme Conditions / government announcements |

No API key is required for these government sources.

## Architecture

```text
Cloud Scheduler
      ↓ every minute
Cloud Run /check
      ↓
HKO / EDB / GovHK
      ↓
Source health validation
      ↓
Event normalization
      ↓
Firestore durable state
      ↓
Notification whitelist + deduplication
      ↓ only when a new actionable state appears
CallMeBot
      ↓
WhatsApp
```

## Notification safety in v0.3

The service is intentionally conservative.

1. **Durable Firestore state** — Cloud Run restarts do not erase notification history.
2. **Fail closed** — if any official source or the state store is unavailable, no WhatsApp alert is sent.
3. **Notification whitelist** — only actionable states can notify: `PRE_T8`, `T8`, `T9`, `T10`, `RED_RAIN`, `BLACK_RAIN`, `EXTREME_CONDITIONS`, and `SUSPENDED`.
4. **Informational states do not notify** — T1, T3 and Amber Rain are tracked but do not send WhatsApp messages.
5. **Duplicate prevention** — the same event status is not sent repeatedly every minute.
6. **Silent bootstrap** — `BOOTSTRAP_SILENT=true` records the first observed state without sending an old/already-active alert.
7. **Delivery retry behavior** — a status is only marked as notified after CallMeBot reports a successful HTTP send.

Cloud Scheduler may call `/check` every minute, but CallMeBot is called only when the rules above allow it.

## Human-readable WhatsApp messages

Example:

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

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service summary |
| GET | `/health` | Cloud Run health + state backend |
| GET | `/sources` | Verify every official source independently |
| POST | `/check` | Poll sources and conditionally notify |
| POST | `/test-notification` | Send / preview a test WhatsApp message |

`GET /sources` is the best way to distinguish “no current event” from “source failed”. A healthy response has `source_health: "ok"` and every source has `ok: true`.

---

# Google Cloud deployment

The repository is designed to deploy from:

```text
https://github.com/chienchitung/hk-weather-whatsapp-poc
```

Recommended Cloud Run / Firestore region: `asia-east2` (Hong Kong). Firestore Native mode supports `asia-east2`. 

## 1. Select the project and verify billing

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud billing projects describe YOUR_PROJECT_ID
```

Confirm `billingEnabled: true`.

## 2. Clone the repository

```bash
cd ~
rm -rf hk-weather-whatsapp-poc
git clone https://github.com/chienchitung/hk-weather-whatsapp-poc.git
cd hk-weather-whatsapp-poc
```

## 3. Enable required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com
```

## 4. Grant the source-build service account permission

Source deployment may use the Compute Engine default service account. Check it with:

```bash
gcloud builds get-default-service-account
```

Then grant Cloud Run Builder:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$(gcloud builds get-default-service-account)" \
  --role="roles/run.builder"
```

This step addresses the common `Build failed because the default service account is missing required IAM permissions` error encountered during `gcloud run deploy --source .`.

## 5. Create Firestore

Check whether a database already exists:

```bash
gcloud firestore databases list
```

If the project does not have a default Firestore database, create one in Hong Kong:

```bash
gcloud firestore databases create \
  --location=asia-east2 \
  --type=firestore-native
```

The application uses the default Firestore database and collection:

```text
hk_weather_notification_state
```

## 6. Grant the Cloud Run runtime account Firestore access

Get the runtime service account after deployment, or use the project's default Compute service account if that is what Cloud Run uses. For the default Compute account:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user"
```

## 7. First deployment: safe DRY RUN

Use a single-line command if Cloud Shell line continuations are causing problems:

```bash
gcloud run deploy hk-weather-whatsapp-poc --source . --region asia-east2 --allow-unauthenticated --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true,STATE_BACKEND=firestore
```

A successful deployment ends with a `Service URL`.

## 8. Verify the service

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')

echo "$SERVICE_URL"
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl -X POST "$SERVICE_URL/check"
```

Expected checks:

- `/health` → `status: ok` and `state_backend: firestore`
- `/sources` → `source_health: ok`
- every listed source → `ok: true`
- `/check` → `errors: []`

Opening the bare service URL is also supported in v0.3; it returns a small service summary instead of a 404.

## 9. Configure CallMeBot

Keep credentials out of GitHub. For a PoC, Cloud Run environment variables work; Secret Manager is preferred for longer-term use.

After CallMeBot activation, configure:

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars CALLMEBOT_PHONE=YOUR_PHONE,CALLMEBOT_API_KEY=YOUR_KEY,DRY_RUN=false,BOOTSTRAP_SILENT=true,STATE_BACKEND=firestore
```

Then test:

```bash
curl -X POST "$SERVICE_URL/test-notification"
```

If CallMeBot returns HTTP 200 but WhatsApp does not arrive, reactivate the CallMeBot WhatsApp permission and test again.

## 10. Configure Cloud Scheduler

Create one HTTP job that calls `/check` every minute:

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

Test it manually:

```bash
gcloud scheduler jobs run hk-weather-check --location=asia-east2
```

Remember: **every-minute polling does not mean every-minute WhatsApp notifications**.

---

# Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export STATE_BACKEND=local
export DRY_RUN=true
python app.py
```

Run tests:

```bash
pytest -q
```

## Environment variables

| Variable | Purpose |
|---|---|
| `DRY_RUN` | `true` prevents real WhatsApp sending |
| `BOOTSTRAP_SILENT` | suppresses first-seen active state |
| `STATE_BACKEND` | `local` or `firestore` |
| `FIRESTORE_COLLECTION` | Firestore collection name |
| `CALLMEBOT_PHONE` | WhatsApp phone registered with CallMeBot |
| `CALLMEBOT_API_KEY` | CallMeBot API key |
| `RSS_MAX_AGE_HOURS` | RSS lookback window |
| `REQUEST_TIMEOUT_SECONDS` | HTTP source timeout |

## Remaining limitations

- CallMeBot is appropriate for personal / small PoC use, not an enterprise mass-notification platform.
- Official wording can change; keyword patterns should be validated during real weather events.
- Explicit warning cancellation / recovery messages are not yet fully modeled.
- For multiple recipients, use a recipient-management layer and eventually WhatsApp Business Cloud API.

## Official references

- HKO Open Data API: https://data.weather.gov.hk/weatherAPI/opendata/weather.php
- EDB RSS: https://www.edb.gov.hk/tc/whats_new_rss.xml
- HKSAR Government RSS: https://www.info.gov.hk/gia/rss/general_zh.xml
- Google Cloud Run: https://cloud.google.com/run
- Google Cloud Scheduler: https://cloud.google.com/scheduler
- Firestore: https://cloud.google.com/firestore

## Disclaimer

This is a technical PoC. Official HKSAR Government, Hong Kong Observatory, Education Bureau, and employer instructions remain the authoritative source during severe weather.