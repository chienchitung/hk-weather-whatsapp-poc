# 香港惡劣天氣／停課 WhatsApp 通知

英文版：[README.md](README.md)

這是一個部署在 Google Cloud Run 的自動通知服務，會定期檢查香港官方公開資訊，只有在出現真正需要注意的新事件時，才透過 CallMeBot 發送真人可讀的 WhatsApp 通知。

## v0.4 架構

```text
Cloud Scheduler
      ↓ 每分鐘
Cloud Run /check
      ↓
官方來源彼此獨立檢查
      ├─ 香港天文台警告摘要
      ├─ 香港天文台特別天氣提示
      ├─ 香港教育局 RSS
      └─ 香港政府新聞公報 RSS
      ↓
Firestore 狀態與去重
      ↓
只有新的重要事件
      ↓
收件人
      ├─ Google Sheet（少量多人建議）
      └─ 單一環境變數收件人（fallback）
      ↓
CallMeBot
      ↓
WhatsApp
```

## 通知邏輯

Cloud Scheduler 可以每分鐘呼叫 `/check`，但不代表每分鐘發 WhatsApp。

目前只有以下事件會主動通知：

- Pre-No. 8
- T8 / T9 / T10
- 紅色暴雨
- 黑色暴雨
- 極端情況
- 教育局停課

T1、T3、黃色暴雨只會作為背景狀態記錄，不主動通知。

### 官方來源改為彼此獨立

v0.4 不再使用「任何一個來源失敗就全部不通知」的邏輯。

每個官方來源各自處理。例如：

```text
教育局 RSS 暫時失敗
香港天文台成功讀到 T8
→ T8 仍然正常通知
```

某個來源失敗時：

- 該來源當次不產生新事件。
- 不會把該來源之前的狀態誤判成解除。
- 其他正常來源照常判斷、照常通知。

如果某個來源讀取正常，而且之前存在的事件已經不再出現，系統會「靜默」把該事件重設為 inactive，不另外發解除通知。這樣下一次真的再次出現同類事件時，仍可以正常通知。

## Firestore 去重

Firestore 永久保存目前事件與已通知狀態，所以 Cloud Run restart、scale to zero 或更換 instance 後，不會忘記之前處理過的事件。

系統也會保存 bootstrap marker，因此在平靜天氣部署時，不會造成未來第一次真的 T8 被誤當成首次啟動而忽略。

## 官方來源

| 官方來源 | 用途 |
|---|---|
| 香港天文台 `warnsum` API | 熱帶氣旋與暴雨警告 |
| 香港天文台 `swt` API | 八號風球前特別天氣提示 |
| 香港教育局 RSS | 正式停課公告 |
| 香港政府新聞公報 RSS | 極端情況與政府特別公告 |

以上香港政府公開來源都不需要 API Key。

---

# Google Sheet 多人收件人管理

如果收件人不多，又希望非技術使用者自行維護，建議直接使用 Google Sheet。

建立一個工作表 tab：

```text
Recipients
```

第一列欄位請固定使用：

| name | phone | api_key | enabled | group | language |
|---|---|---|---|---|---|
| Jackie | 8869XXXXXXXX | XXXXXXX | TRUE | HK Office | zh-TW |
| Amy | 8529XXXXXXX | XXXXXXX | TRUE | HK Office | zh-TW |
| Ben | 8526XXXXXXX | XXXXXXX | FALSE | HK Office | en |

系統只會讀取：

```text
enabled = TRUE
```

且 `phone`、`api_key` 都有填寫的列。

## 為什麼 API key 可以直接放 Google Sheet

這個方案是針對「少量內部收件人、希望非技術人員可自行維護」的情境。

CallMeBot 每一位收件人都有自己的 phone + API key，因此直接放 Sheet 最容易維護，不必由原始開發者持續登入 Google Cloud Secret Manager 更新。

這是方便性與安全性的取捨，因此建議：

- Sheet 只開放給可信任的管理者。
- 一般使用者不要給 Edit 權限。
- 可以將 `api_key` 欄位設成 protected range，避免誤改。

## 不要讓自動化綁在某一位員工的個人帳號

如果未來原始開發者離職或不再維護，自動化仍應能正常運作。

因此 Google Sheet 最好：

- 由公司共用／功能型 Google Workspace 帳號持有，或
- 放在 Google Shared Drive。

Cloud Run 執行時不是靠某個人的瀏覽器登入，而是使用 GCP Project 裡的 runtime service account。

只要把 Google Sheet 分享給 Cloud Run 的 runtime service account（Viewer 即可），Cloud Run 就可以持續讀取 Sheet 中最新的收件人資料。

## 找出 Cloud Run service account

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
echo "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
```

把輸出的 email 加到 Google Sheet 分享名單：

```text
Share → Viewer
```

## 啟用 Google Sheets API

```bash
gcloud services enable sheets.googleapis.com
```

Google Sheet URL 例如：

```text
https://docs.google.com/spreadsheets/d/1ABCDEF123456/edit
```

其中 Spreadsheet ID 是：

```text
1ABCDEF123456
```

設定 Cloud Run：

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars RECIPIENTS_SHEET_ID=YOUR_SPREADSHEET_ID,RECIPIENTS_SHEET_RANGE=Recipients!A:F
```

只要 `RECIPIENTS_SHEET_ID` 有設定，系統就會以 Google Sheet 當收件人來源。

如果沒有設定 Sheet ID，才會 fallback 回原本的：

```text
CALLMEBOT_PHONE
CALLMEBOT_API_KEY
```

---

# Google Cloud 部署／升級

## 1. 啟用必要 API

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com \
  sheets.googleapis.com
```

## 2. Cloud Build 權限

若部署時出現：

```text
Build failed because the default service account is missing required IAM permissions
```

先確認：

```bash
gcloud builds get-default-service-account
```

再授權：

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$(gcloud builds get-default-service-account)" \
  --role="roles/run.builder"
```

## 3. Firestore

```bash
gcloud firestore databases list
```

如果沒有 `(default)`：

```bash
gcloud firestore databases create --location=asia-east2 --type=firestore-native
```

給 runtime service account Firestore 權限：

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user"
```

## 4. 第一次部署

```bash
gcloud run deploy hk-weather-whatsapp-poc --source . --region asia-east2 --allow-unauthenticated --max-instances=1 --concurrency=1 --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true,STATE_BACKEND=firestore
```

第一次乾淨部署才建議使用 `--set-env-vars`。

## 5. 已有服務升級

你目前已經有 CallMeBot 設定，所以升級請使用 `--update-env-vars`：

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

這樣不會把原本 Cloud Run 的其他環境變數清掉。

## 6. 取得 SERVICE_URL

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')

echo "$SERVICE_URL"
```

如果看到：

```text
curl: (3) URL rejected: No host part in the URL
```

代表 `$SERVICE_URL` 沒成功設定，不是 Cloud Run endpoint 本身壞掉。

## 7. 驗證 v0.4.0

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl -X POST "$SERVICE_URL/check"
```

`/health` 應該看到：

```json
{
  "status": "ok",
  "version": "0.4.0",
  "state_backend": "firestore"
}
```

`/sources` 會個別顯示四個官方來源。

即使：

```text
source_health = degraded
```

也不代表所有通知停止；正常來源仍可以發出真正的新事件通知。

## 8. 正式單人通知

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars CALLMEBOT_PHONE=YOUR_PHONE,CALLMEBOT_API_KEY=YOUR_KEY,DRY_RUN=false
```

## 9. 正式 Google Sheet 多人模式

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars RECIPIENTS_SHEET_ID=YOUR_SPREADSHEET_ID,RECIPIENTS_SHEET_RANGE=Recipients!A:F,DRY_RUN=false
```

測試：

```bash
curl -X POST "$SERVICE_URL/test-notification"
```

API 回傳中會顯示：

```text
recipient_count
sent_count
results
```

可以確認哪些收件人有成功送達 CallMeBot。

## 10. Cloud Scheduler

如果尚未建立：

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

如果已經存在，不要重複建立。

提醒：

```text
每分鐘 /check ≠ 每分鐘 WhatsApp
```

只有新的重要事件才會進入 CallMeBot。

---

# API 端點

| Method | Endpoint | 用途 |
|---|---|---|
| GET | `/` | 服務基本資訊 |
| GET | `/health` | 版本、Firestore、收件人模式 |
| GET | `/sources` | 個別確認官方來源健康狀態 |
| POST | `/check` | 檢查官方事件並決定是否通知 |
| POST | `/test-notification` | 手動測試 WhatsApp 多人發送 |

## 目前限制

- 暫時不會主動發「警告已解除」WhatsApp；來源恢復正常後只會靜默重設狀態。
- CallMeBot 適合個人或少量人數 PoC，不適合作為大型企業廣播工具。
- 每一位 CallMeBot 收件人都必須自行完成 CallMeBot activation 並取得自己的 API key。
- 官方公告文字可能改變，因此 parsing 規則仍需在真實惡劣天氣事件中持續觀察。

## Disclaimer

這是一個自動化輔助工具。惡劣天氣期間，仍應以香港特區政府、香港天文台、教育局及僱主正式指示為準。
