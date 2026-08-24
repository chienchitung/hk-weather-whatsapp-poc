# 香港惡劣天氣／停課 WhatsApp 通知 PoC

這是一個以香港官方公開資訊為來源的自動通知 PoC。系統會定期檢查香港天文台、教育局及香港政府新聞公報，只有在出現真正需要注意的新事件時，才透過 CallMeBot 發送 WhatsApp。

英文版：[README.md](README.md)

## 目前監控來源

| 官方來源 | 用途 |
|---|---|
| 香港天文台 `warnsum` API | 熱帶氣旋及暴雨警告 |
| 香港天文台 `swt` API | 八號風球前的特別天氣提示 |
| 香港教育局 RSS | 正式停課公告 |
| 香港政府新聞公報 RSS | 極端情況及政府特別公告 |

上述香港政府資料來源不需要 API Key。

## 系統架構

```text
Cloud Scheduler
      ↓ 每分鐘
Cloud Run /check
      ↓
HKO / EDB / GovHK
      ↓
確認所有來源健康
      ↓
事件標準化
      ↓
Firestore 永久狀態
      ↓
通知白名單 + 去重
      ↓ 只有真正的新事件
CallMeBot
      ↓
WhatsApp
```

## v0.3.1 如何避免亂發通知

這一版採取偏保守的通知策略：

1. **Firestore 永久保存狀態**：Cloud Run 重啟、scale to zero 或換 instance 後，不會忘記先前已經通知過的事件。
2. **任何來源失敗就不發通知（fail closed）**：如果 HKO、EDB、GovHK 任一來源抓取失敗，當次 `/check` 不會更新狀態，也不會呼叫 CallMeBot。
3. **Firestore 無法讀寫也不發通知**：狀態資料庫是安全機制的一部分，資料庫異常時寧可不通知，也不要誤報。
4. **只有白名單事件會通知**：`PRE_T8`、T8、T9、T10、紅雨、黑雨、極端情況、教育局停課。
5. **T1、T3、黃雨只記錄、不主動通知**：這些資訊保留作為狀態背景，但不打擾使用者。
6. **同一狀態只通知一次**：Cloud Scheduler 即使每分鐘跑一次，只要狀態沒有改變，就不會再次傳 WhatsApp。
7. **部署基準與未來真事件分開**：系統會在 Firestore 寫入 `bootstrap_complete`。因此部署當下就算沒有任何警報，幾天後第一次真的發生 T8，仍會正常通知，不會被誤當成首次啟動而略過。
8. **CallMeBot 真正成功後才標記為已通知**：若傳送失敗，系統不會把該狀態誤記成已送達。

因此：

```text
每分鐘 Cloud Run 檢查 ≠ 每分鐘 WhatsApp 通知
```

正常範例：

```text
10:01 無事件 → 不通知
10:02 無事件 → 不通知
10:30 T3 → 只記錄，不通知
11:00 Pre-T8 → 通知一次
11:01 Pre-T8 → 不通知
12:00 T8 → 通知一次
12:01 T8 → 不通知
```

## 真人閱讀的 WhatsApp 格式

例如 T8：

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

停課：

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

## API 端點

| Method | Endpoint | 用途 |
|---|---|---|
| GET | `/` | 服務基本資訊 |
| GET | `/health` | Cloud Run 與 state backend 狀態 |
| GET | `/sources` | 個別確認四個官方來源是否成功 |
| POST | `/check` | 正式檢查並依規則決定是否通知 |
| POST | `/test-notification` | 手動測試 WhatsApp |

### 確認所有官方來源

```bash
curl "$SERVICE_URL/sources"
```

健康狀態應看到：

```json
{
  "source_health": "ok",
  "sources": [
    {"source": "香港天文台－警告摘要", "ok": true},
    {"source": "香港天文台－特別天氣提示", "ok": true},
    {"source": "香港教育局－最新消息", "ok": true},
    {"source": "香港政府新聞公報", "ok": true}
  ],
  "errors": []
}
```

`events_found: 0` 的意思是「成功抓到來源，只是目前沒有符合條件的事件」，不是抓取失敗。

---

# Google Cloud 從頭部署

Repository：

```text
https://github.com/chienchitung/hk-weather-whatsapp-poc
```

建議 Cloud Run 與 Firestore 都使用香港 `asia-east2`。

## 1. 確認 Project 與 Billing

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud billing projects describe YOUR_PROJECT_ID
```

確認：

```text
billingEnabled: true
```

## 2. Clone 最新版本

如果 Cloud Shell 已經 clone 過：

```bash
cd ~/hk-weather-whatsapp-poc
git pull
```

如果要重新開始：

```bash
cd ~
rm -rf hk-weather-whatsapp-poc
git clone https://github.com/chienchitung/hk-weather-whatsapp-poc.git
cd hk-weather-whatsapp-poc
```

## 3. 啟用 API

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com
```

## 4. Cloud Build IAM

我們實際部署時曾遇到：

```text
Build failed because the default service account is missing required IAM permissions
```

先確認 Cloud Build 使用的帳號：

```bash
gcloud builds get-default-service-account
```

再給它 Cloud Run Builder：

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:$(gcloud builds get-default-service-account)" \
  --role="roles/run.builder"
```

## 5. 建立 Firestore

先確認：

```bash
gcloud firestore databases list
```

如果還沒有 `(default)` database：

```bash
gcloud firestore databases create \
  --location=asia-east2 \
  --type=firestore-native
```

Firestore 的香港區域支援 `asia-east2`。

系統會自動建立 collection：

```text
hk_weather_notification_state
```

不需要手動建立 collection。

## 6. 給 Cloud Run Firestore 權限

若 Cloud Run 使用預設 Compute service account：

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user"
```

## 7. 先用 DRY RUN 部署

為避免 Cloud Shell 換行符號操作錯誤，第一次建議直接使用單行：

```bash
gcloud run deploy hk-weather-whatsapp-poc --source . --region asia-east2 --allow-unauthenticated --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true,STATE_BACKEND=firestore
```

成功後會得到：

```text
Service URL: https://...
```

## 8. 設定 SERVICE_URL

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')

echo "$SERVICE_URL"
```

之前看到：

```text
curl: (3) URL rejected: No host part in the URL
```

就是因為 `$SERVICE_URL` 尚未被設定，而不是 Cloud Run 本身壞掉。

## 9. 驗證新版

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl -X POST "$SERVICE_URL/check"
```

`/health` 應顯示：

```json
{
  "status": "ok",
  "version": "0.3.1",
  "state_backend": "firestore"
}
```

第一次 `/check` 正常應出現：

```text
decision: bootstrap_completed_no_notification
```

這就是建立 Firestore 安全基準，不會發 WhatsApp。

之後每次正常執行則為：

```text
decision: completed
```

## 10. CallMeBot

確認 Cloud Run、Firestore、來源都正常後，再開啟正式通知。

不要把真實 API Key commit 到 GitHub。

PoC 可以先使用 Cloud Run environment variables；較正式建議改用 Secret Manager。

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars CALLMEBOT_PHONE=YOUR_PHONE,CALLMEBOT_API_KEY=YOUR_KEY,DRY_RUN=false,BOOTSTRAP_SILENT=true,STATE_BACKEND=firestore
```

測試：

```bash
curl -X POST "$SERVICE_URL/test-notification"
```

若 CallMeBot API 回 HTTP 200，但 WhatsApp 沒收到，重新在 WhatsApp 傳送 CallMeBot 授權訊息後再測。

## 11. Cloud Scheduler

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

手動測：

```bash
gcloud scheduler jobs run hk-weather-check --location=asia-east2
```

查看：

```bash
gcloud scheduler jobs list --location=asia-east2
```

---

# 本機開發

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export STATE_BACKEND=local
export DRY_RUN=true
python app.py
```

測試：

```bash
pytest -q
```

## 環境變數

| 變數 | 說明 |
|---|---|
| `DRY_RUN` | `true` 時不真正送 WhatsApp |
| `BOOTSTRAP_SILENT` | 啟動時建立安全基準 |
| `STATE_BACKEND` | `local` 或 `firestore` |
| `FIRESTORE_COLLECTION` | Firestore collection 名稱 |
| `CALLMEBOT_PHONE` | CallMeBot 啟用的電話 |
| `CALLMEBOT_API_KEY` | CallMeBot API Key |
| `RSS_MAX_AGE_HOURS` | RSS 公告回看時間 |
| `REQUEST_TIMEOUT_SECONDS` | 官方來源 timeout |

## 尚未完成的功能

- 警告解除（例如 T8 → 完全取消）尚未完整建模成 `✅ 已解除` 通知。
- CallMeBot 適合個人／少量 PoC，不適合作為大型企業群發服務。
- 如果未來需要多人收件，建議加入 Google Sheet 收件人管理層，正式環境再轉 WhatsApp Business Cloud API。
- 官方公告措辭可能改變，因此 regex 規則仍應在真實惡劣天氣事件期間持續驗證。

## Disclaimer

這是技術 PoC。惡劣天氣期間，仍應以香港特區政府、香港天文台、教育局及僱主正式指示為準。
