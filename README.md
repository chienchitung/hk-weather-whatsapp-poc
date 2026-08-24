# HK Weather & School WhatsApp Notification

[繁體中文版](README.zh-TW.md)

A Google Cloud Run service that monitors official Hong Kong severe-weather and school-suspension information and sends human-readable WhatsApp alerts through CallMeBot.

## Architecture

```text
Cloud Scheduler (every minute)
        ↓
Cloud Run /check
        ↓
Official sources checked independently
        ├─ HKO warning summary
        ├─ HKO Special Weather Tips
        ├─ Education Bureau RSS
        └─ HKSAR Government RSS
        ↓
Firestore durable state + deduplication
        ↓
Only a new actionable event
        ↓
Recipients
        ├─ Google Sheet (multi-recipient)
        └─ environment variables (single-recipient fallback)
        ↓
CallMeBot → WhatsApp
```

## Official sources

| Source | Used for |
|---|---|
| HKO `warnsum` | Tropical cyclone and rainstorm warning states |
| HKO `swt` | Pre-No. 8 indications / Special Weather Tips |
| Education Bureau RSS | Official class-suspension notices |
| HKSAR Government RSS | Extreme Conditions / special government notices |

No API key is required for these government sources.

## Notification matrix

Cloud Scheduler may call `/check` every minute, but WhatsApp is **not** sent every minute. A message is sent only when a newly observed state is on the notification whitelist and has not already been successfully notified.

| Official state / message | Normalized status | WhatsApp? | Notes |
|---|---|---:|---|
| Tropical Cyclone Signal No. 1 | `T1` | No | Stored as context only |
| Tropical Cyclone Signal No. 3 | `T3` | No | Stored as context only |
| HKO indicates No. 8 may be issued | `PRE_T8` | **Yes** | Read from HKO Special Weather Tips |
| Tropical Cyclone Signal No. 8 | `T8` | **Yes** | Actionable alert |
| Tropical Cyclone Signal No. 9 | `T9` | **Yes** | Actionable alert |
| Tropical Cyclone Signal No. 10 | `T10` | **Yes** | Actionable alert |
| Amber Rainstorm Warning | `AMBER_RAIN` | No | Stored as context only |
| Red Rainstorm Warning | `RED_RAIN` | **Yes** | Actionable alert |
| Black Rainstorm Warning | `BLACK_RAIN` | **Yes** | Actionable alert |
| Extreme Conditions | `EXTREME_CONDITIONS` | **Yes** | Government special arrangement |
| Education Bureau class suspension | `SUSPENDED` | **Yes** | Official school suspension |
| Very Hot Weather Warning (`WHOT`) | not normalized | No | Source can be read successfully but this warning is intentionally outside the current notification scope |
| Cold Weather / Thunderstorm / Strong Monsoon and other HKO warnings | not normalized | No | Not part of the current work/school disruption scope |

Example:

```text
10:30 T3        → stored, no message
11:00 Pre-T8    → WhatsApp once
11:01 Pre-T8    → no duplicate
12:00 T8        → WhatsApp once
12:01 T8        → no duplicate
```

## Source failure behavior

Sources are processed independently. A failure in one source does **not** block a valid event from another healthy source.

Example:

```text
EDB RSS unavailable
HKO reports a new T8
→ T8 is still sent to CallMeBot
```

A failed source keeps its previous state unchanged until that source becomes healthy again, so a temporary outage is not mistaken for an event cancellation.

## Human-readable notification example

```text
🔴 Hong Kong Weather Alert | No. 8 signal is in force

八號東南烈風或暴風信號
目前狀況：八號烈風或暴風信號
前一狀況：三號強風信號

📌 請避免不必要外出，留意交通及安全情況；工作安排請依公司內部惡劣天氣政策執行。

發布單位：香港天文台
更新時間：2026-08-24 16:10 HKT
官方公告：https://...
```

## Google Sheet recipients

For a small team, Google Sheet is the intended non-technical maintenance interface.

Create a sheet tab named `Recipients` with **exactly these three columns**:

| name | phone | api_key |
|---|---|---|
| Jackie | 8869XXXXXXXX | XXXXXXX |
| Amy | 8529XXXXXXX | XXXXXXX |

Rules:

- Every non-empty row with both `phone` and `api_key` is treated as active.
- To add a recipient, add a new row.
- To remove a recipient, delete the row.
- Each recipient must activate CallMeBot and use their own CallMeBot API key.
- Keep access to this Sheet restricted because it contains API keys.

Current spreadsheet ID:

```text
1IA5MgL130nSUvhCan2NIhIv7bpxZU1hNK4AChUjMEBI
```

Recommended range:

```text
Recipients!A:C
```

### Give Cloud Run access to the Sheet

Find the runtime service account:

```bash
PROJECT_NUMBER=$(gcloud projects describe hk-weather-whatsapp-poc --format='value(projectNumber)')
echo "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
```

Share the Google Sheet with that service-account email as **Viewer**. Cloud Run then reads the Sheet independently of any interactive personal login session.

Enable the Sheets API:

```bash
gcloud services enable sheets.googleapis.com
```

Configure the deployed service:

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars RECIPIENTS_SHEET_ID=1IA5MgL130nSUvhCan2NIhIv7bpxZU1hNK4AChUjMEBI,RECIPIENTS_SHEET_RANGE=Recipients!A:C,DRY_RUN=false
```

When `RECIPIENTS_SHEET_ID` is present, Google Sheet recipients take precedence over the single-recipient `CALLMEBOT_PHONE` / `CALLMEBOT_API_KEY` fallback.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Service summary |
| GET | `/health` | Version, state backend and recipient mode |
| GET | `/sources` | Check each official source independently |
| POST | `/check` | Poll sources and conditionally notify |
| POST | `/test-notification` | Send a test message to configured recipients |

Check source health:

```bash
curl "$SERVICE_URL/sources"
```

`events_found: 0` means the source was read successfully but no matching notification event is active.

## Google Cloud deployment / upgrade

Get the latest code:

```bash
cd ~/hk-weather-whatsapp-poc
git pull
```

Enable required APIs:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com cloudscheduler.googleapis.com sheets.googleapis.com
```

For an existing service, preserve current environment variables by using `--update-env-vars`:

```bash
gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated \
  --max-instances=1 \
  --concurrency=1 \
  --update-env-vars STATE_BACKEND=firestore,BOOTSTRAP_SILENT=true
```

Get the URL:

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc --region asia-east2 --format='value(status.url)')
echo "$SERVICE_URL"
```

Verify:

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl -X POST "$SERVICE_URL/check"
```

## Firestore safety

Firestore stores current and previously notified event state, so Cloud Run restart / scale-to-zero does not cause repeated alerts. A healthy source that no longer reports an event silently resets that event state, allowing a future new occurrence to notify again. A failed source does not reset its state.

## Cloud Scheduler

Example every-minute job:

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

Every-minute polling does not mean every-minute WhatsApp messages.

## Limitations

- Warning cancellation / recovery messages are currently reset silently rather than sent as separate `cleared` WhatsApp notifications.
- CallMeBot is appropriate for a personal or small-team PoC, not large enterprise broadcasting.
- Official wording can change, so parsing patterns should be validated during real severe-weather events.

## Disclaimer

This is a technical PoC. HKSAR Government, Hong Kong Observatory, Education Bureau and employer instructions remain authoritative during severe weather.
