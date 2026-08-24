# HK Weather & School WhatsApp Notification PoC

A proof of concept that monitors official Hong Kong severe-weather and school-suspension information and sends human-readable WhatsApp alerts through CallMeBot only when a meaningful new actionable event is detected.

Traditional Chinese: [README.zh-TW.md](README.zh-TW.md)

## Architecture

```text
Cloud Scheduler (every minute)
        ↓
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
Whitelist + deduplication
        ↓ only for a new actionable event
CallMeBot → WhatsApp
```

## Official sources

| Source | Purpose |
|---|---|
| HKO `warnsum` API | Tropical cyclone / rainstorm warning states |
| HKO `swt` API | Pre-No. 8 / Special Weather Tips |
| Education Bureau RSS | Official class-suspension notices |
| HKSAR Government RSS | Extreme Conditions / government announcements |

No API key is required for these government sources.

## Safety model (v0.3.1)

This version is deliberately conservative:

- **Firestore state** survives Cloud Run restart / scale-to-zero.
- **Fail closed**: if any official source fails, no state change and no WhatsApp notification occurs.
- **Fail closed**: if Firestore cannot be read or written, no WhatsApp notification occurs.
- **Notification whitelist**: only `PRE_T8`, `T8`, `T9`, `T10`, `RED_RAIN`, `BLACK_RAIN`, `EXTREME_CONDITIONS`, and `SUSPENDED` can notify.
- **T1, T3 and Amber Rain are informational only** and do not send WhatsApp.
- **One status, one notification**: the last successfully notified status is stored in Firestore.
- **Persistent bootstrap marker**: deployment on a quiet day is recorded separately, so the first real future T8 is not accidentally suppressed.
- **Delivery-aware state**: a state is only marked notified after CallMeBot returns a successful HTTP send.
- **Recommended single-worker Cloud Run**: `--max-instances=1 --concurrency=1` reduces overlapping executions for this small PoC.

Therefore:

```text
Every-minute Cloud Run polling != every-minute WhatsApp messages
```

Example:

```text
10:01 No event → no notification
10:30 T3 → stored only
11:00 Pre-T8 → notify once
11:01 Pre-T8 → no notification
12:00 T8 → notify once
12:01 T8 → no notification
```

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service summary |
| GET | `/health` | Service version + state backend |
| GET | `/sources` | Verify all official sources independently |
| POST | `/check` | Poll sources and conditionally notify |
| POST | `/test-notification` | Test CallMeBot / WhatsApp |

A healthy `/sources` response has `source_health: "ok"` and every source has `ok: true`. `events_found: 0` means the source was read successfully but no matching event is active.

---

# Google Cloud deployment

Repository:

```text
https://github.com/chienchitung/hk-weather-whatsapp-poc
```

Recommended Cloud Run and Firestore location: `asia-east2` (Hong Kong).

## 1. Verify project and billing

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud billing projects describe YOUR_PROJECT_ID
```

Confirm `billingEnabled: true`.

## 2. Get the latest repository

Existing Cloud Shell clone:

```bash
cd ~/hk-weather-whatsapp-poc
git pull
```

Fresh clone:

```bash
cd ~
rm -rf hk-weather-whatsapp-poc
git clone https://github.com/chienchitung/hk-weather-whatsapp-poc.git
cd hk-weather-whatsapp-poc
```

## 3. Enable APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com
```

## 4. Cloud Build IAM

During the first source deployment we encountered:

```text
Build failed because the default service account is missing required IAM permissions
```

Check the build account:

```bash
gcloud builds get-default-service-account
```

Grant Cloud Run Builder:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$(gcloud builds get-default-service-account)" \
  --role="roles/run.builder"
```

## 5. Create Firestore

```bash
gcloud firestore databases list
```

If `(default)` does not exist:

```bash
gcloud firestore databases create --location=asia-east2 --type=firestore-native
```

The application automatically creates the collection:

```text
hk_weather_notification_state
```

## 6. Grant runtime Firestore permission

If Cloud Run uses the default Compute service account:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user"
```

## 7A. First deployment

Use DRY RUN first:

```bash
gcloud run deploy hk-weather-whatsapp-poc --source . --region asia-east2 --allow-unauthenticated --max-instances=1 --concurrency=1 --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true,STATE_BACKEND=firestore
```

`--set-env-vars` replaces the complete environment-variable set, so it is appropriate for a first clean deployment only.

## 7B. Upgrade an existing service without deleting CallMeBot credentials

If the Cloud Run service already contains `CALLMEBOT_PHONE` and `CALLMEBOT_API_KEY`, use `--update-env-vars`, not `--set-env-vars`:

```bash
gcloud run deploy hk-weather-whatsapp-poc --source . --region asia-east2 --allow-unauthenticated --max-instances=1 --concurrency=1 --update-env-vars STATE_BACKEND=firestore,BOOTSTRAP_SILENT=true
```

This preserves the existing CallMeBot variables. Google Cloud's `--set-env-vars` clears existing variables first, while `--update-env-vars` updates only the named keys.

## 8. Get the service URL

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')

echo "$SERVICE_URL"
```

If `curl "$SERVICE_URL/health"` says `No host part in the URL`, `$SERVICE_URL` has not been set yet.

## 9. Verify v0.3.1

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl -X POST "$SERVICE_URL/check"
```

Expected `/health`:

```json
{"status":"ok","version":"0.3.1","state_backend":"firestore"}
```

The first healthy `/check` against a new Firestore state should return:

```text
decision: bootstrap_completed_no_notification
```

That creates the safe baseline and intentionally sends no WhatsApp message.

Subsequent healthy runs return `decision: completed`.

If any source or Firestore is unhealthy, the decision starts with `fail_closed_...` and `notifications` remains empty.

## 10. CallMeBot

Never commit the real phone number or API key to GitHub. Secret Manager is preferable for a longer-lived deployment.

For an already deployed service:

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars CALLMEBOT_PHONE=YOUR_PHONE,CALLMEBOT_API_KEY=YOUR_KEY,DRY_RUN=false,STATE_BACKEND=firestore
```

Test:

```bash
curl -X POST "$SERVICE_URL/test-notification"
```

If CallMeBot returns HTTP 200 but WhatsApp does not arrive, re-activate CallMeBot permission in WhatsApp and test again.

## 11. Cloud Scheduler

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

Manual test:

```bash
gcloud scheduler jobs run hk-weather-check --location=asia-east2
```

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export STATE_BACKEND=local
export DRY_RUN=true
python app.py
pytest -q
```

## Remaining limitations

- Explicit recovery / cancellation notifications (for example T8 → warning fully cancelled) are not yet fully modeled.
- CallMeBot is suitable for a personal / small PoC, not enterprise mass notification.
- Official wording may change; parsing rules should be observed during real severe-weather events.
- Multiple-recipient production use should eventually move to a recipient-management layer and WhatsApp Business Cloud API.

## Disclaimer

This is a technical PoC. HKSAR Government, Hong Kong Observatory, Education Bureau and employer instructions remain authoritative during severe weather.