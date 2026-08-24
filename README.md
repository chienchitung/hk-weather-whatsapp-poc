# HK Weather & School WhatsApp Notification

[繁體中文版](README.zh-TW.md)

A Google Cloud Run service that monitors official Hong Kong severe-weather and school-suspension information and sends human-readable WhatsApp alerts through CallMeBot.

## v0.4 design

```text
Cloud Scheduler
      ↓ every minute
Cloud Run /check
      ↓
Official sources are checked independently
      ├─ HKO warning summary
      ├─ HKO special weather tips
      ├─ Education Bureau RSS
      └─ HKSAR Government RSS
      ↓
Firestore state / deduplication
      ↓
Actionable new event only
      ↓
Recipients
      ├─ Google Sheet (recommended for a small team)
      └─ single environment-variable recipient (fallback)
      ↓
CallMeBot
      ↓
WhatsApp
```

## Notification behavior

Cloud Scheduler may call `/check` every minute, but WhatsApp is **not** sent every minute.

Only these actionable states can notify:

- Pre-No. 8
- T8 / T9 / T10
- Red Rainstorm
- Black Rainstorm
- Extreme Conditions
- Education Bureau class suspension

T1, T3 and Amber Rain are stored as context but do not notify.

### Independent source handling

Each official source is handled independently.

If one source fails, healthy sources continue to work. Example:

```text
EDB RSS fails
HKO successfully reports T8
→ T8 notification is still sent
```

A failed source does **not** clear its previous state. Its state is left unchanged until that source becomes healthy again.

If a source is healthy and a previously active event is no longer present, that event is silently reset to inactive. This allows a future new episode of the same event to notify again without sending unnecessary recovery messages.

## Durable deduplication

Firestore stores the latest event state. This prevents Cloud Run restart / scale-to-zero from forgetting what was already processed.

A persistent bootstrap marker also prevents deployment on a quiet day from suppressing the first real future T8.

## Official data sources

| Source | Purpose |
|---|---|
| HKO `warnsum` API | Tropical cyclone and rainstorm warnings |
| HKO `swt` API | Pre-No. 8 / special weather tips |
| Education Bureau RSS | Class-suspension announcements |
| HKSAR Government RSS | Extreme Conditions / special government announcements |

No API key is required for these public Hong Kong government sources.

---

# Google Sheet recipient management

For a small team, Google Sheet can be the non-technical administration interface.

Create a spreadsheet tab named `Recipients` with this header row:

| name | phone | api_key | enabled | group | language |
|---|---|---|---|---|---|
| Jackie | 8869XXXXXXXX | XXXXXXX | TRUE | HK Office | zh-TW |
| Amy | 8529XXXXXXX | XXXXXXX | TRUE | HK Office | zh-TW |
| Ben | 8526XXXXXXX | XXXXXXX | FALSE | HK Office | en |

Only rows with `enabled=TRUE` and both `phone` + `api_key` are used.

### Why the API key is stored in the Sheet

For this small-team PoC, each CallMeBot recipient has their own API key. Storing it in the Sheet makes recipient maintenance independent of the original developer.

This is a convenience/security trade-off. Limit Sheet editors to trusted administrators. If possible, protect the `api_key` column from casual editing.

### Important ownership recommendation

Do not make long-term automation depend on a departing employee's personal Google account.

Prefer one of these:

- a company/shared Google Workspace account owns the spreadsheet, or
- the spreadsheet is stored in a Shared Drive.

Cloud Run itself does **not** need an employee to stay logged in. It runs as a Google Cloud service account.

Share the spreadsheet with the Cloud Run runtime service-account email as **Viewer**. Then the service can keep reading updated rows even when the original developer is offline.

### Enable Google Sheets API

```bash
gcloud services enable sheets.googleapis.com
```

Get the runtime service account used by this PoC:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
echo "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
```

Share the Google Sheet with that email as Viewer.

The spreadsheet ID is the value between `/d/` and `/edit` in a Google Sheets URL.

Then configure Cloud Run:

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars RECIPIENTS_SHEET_ID=YOUR_SPREADSHEET_ID,RECIPIENTS_SHEET_RANGE=Recipients!A:F
```

When `RECIPIENTS_SHEET_ID` is configured, Google Sheet becomes the recipient source. If it is empty, the service falls back to `CALLMEBOT_PHONE` + `CALLMEBOT_API_KEY` environment variables.

---

# Google Cloud deployment / upgrade

## Enable APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com \
  sheets.googleapis.com
```

## Cloud Build permission

If source deployment reports that the default service account is missing permissions:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$(gcloud builds get-default-service-account)" \
  --role="roles/run.builder"
```

## Firestore

Check first:

```bash
gcloud firestore databases list
```

If `(default)` does not exist:

```bash
gcloud firestore databases create --location=asia-east2 --type=firestore-native
```

Give the runtime service account Firestore access:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user"
```

## First deployment

```bash
gcloud run deploy hk-weather-whatsapp-poc --source . --region asia-east2 --allow-unauthenticated --max-instances=1 --concurrency=1 --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true,STATE_BACKEND=firestore
```

## Upgrade an existing service

Use `--update-env-vars`, not `--set-env-vars`, so existing CallMeBot credentials are not removed.

```bash
cd ~/hk-weather-whatsapp-poc
git pull

gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated \
  --max-instances=1 \
  --concurrency=1 \
  --update-env-vars STATE_BACKEND=firestore,BOOTSTRAP_SILENT=true
```

## Service URL

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')

echo "$SERVICE_URL"
```

If `curl` reports `URL rejected: No host part in the URL`, `$SERVICE_URL` was not successfully set.

## Verify v0.4.0

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl -X POST "$SERVICE_URL/check"
```

Expected `/health` includes:

```json
{"status":"ok","version":"0.4.0","state_backend":"firestore"}
```

`/sources` shows each official source separately. `source_health=degraded` does not automatically stop healthy-source notifications.

## Enable real notification

Single-recipient fallback:

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars CALLMEBOT_PHONE=YOUR_PHONE,CALLMEBOT_API_KEY=YOUR_KEY,DRY_RUN=false
```

Google Sheet recipient mode:

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars RECIPIENTS_SHEET_ID=YOUR_SPREADSHEET_ID,RECIPIENTS_SHEET_RANGE=Recipients!A:F,DRY_RUN=false
```

Test manually:

```bash
curl -X POST "$SERVICE_URL/test-notification"
```

## Cloud Scheduler

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

If the job already exists, do not create it again.

---

# API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Service info |
| GET | `/health` | Version / state backend / recipient mode |
| GET | `/sources` | Health of each official source |
| POST | `/check` | Fetch sources and notify on a new actionable event |
| POST | `/test-notification` | Manual WhatsApp test |

## Current limitations

- Recovery/cancellation messages are not sent yet; inactive state is reset silently.
- CallMeBot is suitable for personal/small-team PoC use, not enterprise-scale broadcasting.
- Each CallMeBot recipient must activate CallMeBot and supply their own API key.
- Official wording may change; parsers should be reviewed during real severe-weather events.

## Disclaimer

This service is a technical automation aid. During severe weather, official instructions from the HKSAR Government, Hong Kong Observatory, Education Bureau and the employer remain authoritative.
