# HK Weather & School WhatsApp Notification

[繁體中文版](README.zh-TW.md)

A Google Cloud Run service that monitors official Hong Kong severe-weather and school-suspension information and sends human-readable WhatsApp alerts through CallMeBot.

## Architecture

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
Google Sheet recipients
      ↓
CallMeBot → WhatsApp
```

One failed official source does not block valid events from other healthy sources. A failed source keeps its previous state until that source becomes healthy again.

## Notification matrix

| Official state | Normalized state | WhatsApp |
|---|---|---|
| Tropical Cyclone Signal No. 1 | `T1` | No |
| Tropical Cyclone Signal No. 3 | `T3` | No |
| Pre-No. 8 indication | `PRE_T8` | **Yes** |
| Tropical Cyclone Signal No. 8 | `T8` | **Yes** |
| Tropical Cyclone Signal No. 9 | `T9` | **Yes** |
| Tropical Cyclone Signal No. 10 | `T10` | **Yes** |
| Amber Rainstorm | `AMBER_RAIN` | No |
| Red Rainstorm | `RED_RAIN` | **Yes** |
| Black Rainstorm | `BLACK_RAIN` | **Yes** |
| Extreme Conditions | `EXTREME_CONDITIONS` | **Yes** |
| Education Bureau class suspension | `SUSPENDED` | **Yes** |
| Very Hot Weather Warning (`WHOT`) | not normalized | No |
| Cold / thunderstorm / monsoon and other HKO warnings | not normalized | No |

Cloud Scheduler may call `/check` every minute, but WhatsApp is only sent when a new actionable state is detected and it has not already been notified.

## Google Sheet recipients

The `Recipients` sheet only needs three columns:

```text
name | phone | api_key
```

Example:

```text
Jackie | 8869XXXXXXXX | XXXXXXX
Amy    | 8529XXXXXXX  | XXXXXXX
```

A row with both `phone` and `api_key` is treated as active. Add a row to add a user; delete the row to remove a user.

Current Sheet configuration:

```text
RECIPIENTS_SHEET_ID=1IA5MgL130nSUvhCan2NIhIv7bpxZU1hNK4AChUjMEBI
RECIPIENTS_SHEET_RANGE=Recipients!A:C
```

The Sheet must be shared as Viewer with the Cloud Run runtime service account.

## Recipient onboarding test — v0.4.1

Adding a person to the Sheet does not require waiting for a real weather event.

Test one recipient only:

```bash
curl -X POST "$SERVICE_URL/test-recipient?name=Amy"
```

The recipient receives a message clearly marked as a setup test, not a real weather warning. After a successful send, Firestore stores a verification record in:

```text
hk_weather_recipient_status
```

Verification is operational metadata only. It does **not** block real weather notifications: if a person exists in the Sheet, `/check` still attempts to send real actionable alerts to that recipient.

View onboarding status without exposing phone numbers or API keys:

```bash
curl "$SERVICE_URL/recipients"
```

Example response:

```json
{
  "total": 3,
  "verified": 2,
  "unverified": 1,
  "recipients": [
    {"name":"Jackie","verified":true,"last_test_status":"success"},
    {"name":"Amy","verified":true,"last_test_status":"success"},
    {"name":"Peter","verified":false,"last_test_status":null}
  ]
}
```

`POST /test-notification` remains available to test all recipients at once.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service summary |
| GET | `/health` | Version / backend / recipient mode |
| GET | `/sources` | Check official sources independently |
| GET | `/recipients` | Recipient verification summary |
| POST | `/check` | Official polling and conditional notification |
| POST | `/test-notification` | Test all recipients |
| POST | `/test-recipient?name=...` | Test one recipient only |

## Quick test commands

Get the current Cloud Run URL first:

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')

echo "$SERVICE_URL"
```

Then use these commands for routine testing:

```bash
# 1. Verify Cloud Run version and recipient mode
curl "$SERVICE_URL/health"

# 2. Check all official sources
# /sources is GET and the path is lowercase
curl "$SERVICE_URL/sources"

# 3. View recipient verification status
curl "$SERVICE_URL/recipients"

# 4. Send a test message to every valid recipient in the Sheet
curl -X POST "$SERVICE_URL/test-notification"

# 5. Test one recipient only
TEST_RECIPIENT="Jackie"
curl -X POST "$SERVICE_URL/test-recipient?name=$TEST_RECIPIENT"

# 6. Run one real weather check manually
# WhatsApp is only sent if a new actionable state is detected
curl -X POST "$SERVICE_URL/check"
```

To test a different new recipient:

```bash
TEST_RECIPIENT="Amy"
curl -X POST "$SERVICE_URL/test-recipient?name=$TEST_RECIPIENT"
```

`/test-notification` sends a setup test to all valid Sheet recipients; `/test-recipient` sends only to the named recipient; `/sources` never sends WhatsApp and is only a source-health check.

## Deploy / upgrade

```bash
cd ~/hk-weather-whatsapp-poc
git pull
```

Enable required APIs:

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  sheets.googleapis.com \
  cloudscheduler.googleapis.com
```

For an existing Cloud Run service, preserve existing environment variables with `--update-env-vars`:

```bash
gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated \
  --max-instances=1 \
  --concurrency=1 \
  --update-env-vars 'STATE_BACKEND=firestore,RECIPIENTS_SHEET_ID=1IA5MgL130nSUvhCan2NIhIv7bpxZU1hNK4AChUjMEBI,RECIPIENTS_SHEET_RANGE=Recipients!A:C,DRY_RUN=false'
```

Get the service URL:

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')
```

Verify v0.4.1:

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl "$SERVICE_URL/recipients"
```

Expected version:

```json
{"version":"0.4.1"}
```

## Cloud Scheduler

Create the job once:

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

If it already exists, update it instead:

```bash
gcloud scheduler jobs update http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

Manual run:

```bash
gcloud scheduler jobs run hk-weather-check --location=asia-east2
```

## State behavior

- Firestore keeps weather notification state across Cloud Run restarts.
- T1, T3 and Amber Rain are stored as context but do not notify.
- When a healthy source no longer reports an event, the event state is silently reset so a future new occurrence can notify again.
- A failed source does not reset its old state and does not block healthy sources.
- CallMeBot delivery failures are recorded in the response; the event is not marked successfully notified unless at least one send succeeds.

## Disclaimer

This is a technical PoC. HKSAR Government, Hong Kong Observatory, Education Bureau and employer instructions remain authoritative during severe weather.
