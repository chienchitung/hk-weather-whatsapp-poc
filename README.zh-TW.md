# 香港惡劣天氣／停課 WhatsApp 通知 PoC

這是一個以香港官方公開資訊為來源的自動通知 PoC。系統會定期檢查香港天文台、教育局及香港政府新聞公報，只有在出現真正需要注意的新事件時，才透過 CallMeBot 發送 WhatsApp。

英文版：[README.md](README.md)

## 系統架構

```text
Cloud Scheduler（每分鐘）
        ↓
Cloud Run /check
        ↓
HKO / EDB / GovHK
        ↓
來源健康檢查
        ↓
事件標準化
        ↓
Firestore 永久狀態
        ↓
通知白名單 + 去重
        ↓ 只有真正的新事件
CallMeBot → WhatsApp
```

## 官方來源

| 官方來源 | 用途 |
|---|---|
| 香港天文台 `warnsum` API | 熱帶氣旋及暴雨警告 |
| 香港天文台 `swt` API | 八號風球前的特別天氣提示 |
| 香港教育局 RSS | 正式停課公告 |
| 香港政府新聞公報 RSS | 極端情況及政府特別公告 |

上述香港政府資料來源都不需要 API Key。

## v0.3.1 如何避免亂發通知

這一版採取偏保守的策略：

- **Firestore 永久保存通知狀態**：Cloud Run restart、scale to zero 或更換 instance 後，不會忘記之前已通知過什麼。
- **任何官方來源失敗就不通知（fail closed）**：HKO、EDB、GovHK 任一來源異常，該次 `/check` 不更新狀態、不呼叫 CallMeBot。
- **Firestore 無法讀寫也不通知**：狀態資料庫異常時寧可不發，也不要誤報。
- **只有通知白名單會發 WhatsApp**：Pre-T8、T8、T9、T10、紅雨、黑雨、極端情況、教育局停課。
- **T1、T3、黃雨只記錄，不主動通知**。
- **同一狀態只通知一次**：每分鐘檢查不等於每分鐘通知。
- **永久 bootstrap 標記**：即使部署當下沒有任何事件，系統仍會寫入 `bootstrap_complete`；未來第一次真的出現 T8 時會正常通知，不會被錯當成首次啟動。
- **CallMeBot 成功後才標記已通知**：若傳送失敗，不會誤記為已送達。
- **建議 Cloud Run 限制為單 worker**：`--max-instances=1 --concurrency=1`，降低排程重疊或手動重複呼叫造成同時處理的風險。

例如：

```text
10:01 無事件 → 不通知
10:30 T3 → 只記錄
11:00 Pre-T8 → 通知一次
11:01 Pre-T8 → 不通知
12:00 T8 → 通知一次
12:01 T8 → 不通知
```

## 真人閱讀的通知格式

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

## API 端點

| Method | Endpoint | 用途 |
|---|---|---|
| GET | `/` | 服務基本資訊 |
| GET | `/health` | 服務版本及 state backend |
| GET | `/sources` | 個別確認所有官方來源 |
| POST | `/check` | 正式檢查並依規則決定是否通知 |
| POST | `/test-notification` | 手動測試 WhatsApp |

### 如何確認所有來源都成功

```bash
curl "$SERVICE_URL/sources"
```

健康時應看到：

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

`events_found: 0` 表示「來源成功，只是目前沒有事件」，不是抓取失敗。

---

# Google Cloud 部署

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

## 2. 取得最新程式

Cloud Shell 已經 clone 過：

```bash
cd ~/hk-weather-whatsapp-poc
git pull
```

重新 clone：

```bash
cd ~
rm -rf hk-weather-whatsapp-poc
git clone https://github.com/chienchitung/hk-weather-whatsapp-poc.git
cd hk-weather-whatsapp-poc
```

## 3. 啟用必要 API

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com
```

## 4. Cloud Build IAM

實際部署時曾遇到：

```text
Build failed because the default service account is missing required IAM permissions
```

先確認 build account：

```bash
gcloud builds get-default-service-account
```

再授權：

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

如果沒有 `(default)` database：

```bash
gcloud firestore databases create --location=asia-east2 --type=firestore-native
```

系統之後會自動建立 collection：

```text
hk_weather_notification_state
```

不需要手動建 collection。

## 6. 給 Cloud Run Firestore 權限

若 Cloud Run 使用預設 Compute service account：

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user"
```

## 7A. 第一次乾淨部署

第一次部署可以用：

```bash
gcloud run deploy hk-weather-whatsapp-poc --source . --region asia-east2 --allow-unauthenticated --max-instances=1 --concurrency=1 --set-env-vars DRY_RUN=true,BOOTSTRAP_SILENT=true,STATE_BACKEND=firestore
```

注意：`--set-env-vars` 會先清除原本環境變數，因此只建議在第一次乾淨部署使用。

## 7B. 你目前這種「已經有 CallMeBot 設定」的升級方式

如果 Cloud Run 已經有 `CALLMEBOT_PHONE`、`CALLMEBOT_API_KEY`，請不要再用 `--set-env-vars`，否則會把它們清掉。

請使用：

```bash
gcloud run deploy hk-weather-whatsapp-poc --source . --region asia-east2 --allow-unauthenticated --max-instances=1 --concurrency=1 --update-env-vars STATE_BACKEND=firestore,BOOTSTRAP_SILENT=true
```

`--update-env-vars` 只更新指定的 key，因此會保留既有 CallMeBot 設定。

## 8. 設定 SERVICE_URL

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')

echo "$SERVICE_URL"
```

之前如果看到：

```text
curl: (3) URL rejected: No host part in the URL
```

代表 `$SERVICE_URL` 還沒設定，而不是服務壞掉。

## 9. 驗證新版安全狀態

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl -X POST "$SERVICE_URL/check"
```

`/health` 應該看到：

```json
{"status":"ok","version":"0.3.1","state_backend":"firestore"}
```

新 Firestore 第一次 `/check` 應該看到：

```text
decision: bootstrap_completed_no_notification
```

這表示安全基準已建立，而且不會發 WhatsApp。

之後正常執行為：

```text
decision: completed
```

如果官方來源或 Firestore 有問題，會看到類似：

```text
decision: fail_closed_source_error
```

或：

```text
decision: fail_closed_state_error
```

這時 `notifications` 會保持空白，不會亂發訊息。

## 10. CallMeBot

不要把真實電話與 API Key commit 到 GitHub。長期使用建議改用 Secret Manager。

既有服務可以使用：

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars CALLMEBOT_PHONE=YOUR_PHONE,CALLMEBOT_API_KEY=YOUR_KEY,DRY_RUN=false,STATE_BACKEND=firestore
```

測試：

```bash
curl -X POST "$SERVICE_URL/test-notification"
```

如果 API 回 200 但 WhatsApp 沒收到，重新在 WhatsApp 啟用 CallMeBot 授權後再測。

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

提醒：**每分鐘執行 `/check`，不代表每分鐘發 WhatsApp。**

---

# 本機開發

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export STATE_BACKEND=local
export DRY_RUN=true
python app.py
pytest -q
```

## 尚未完成

- 警告解除（例如 T8 → 警告完全取消）的 `✅ 已解除` 通知尚未完整建模。
- CallMeBot 適合個人／少量 PoC，不適合作為企業大量群發。
- 未來多人收件可以加入 Google Sheet 收件人管理層，正式環境再轉 WhatsApp Business Cloud API。
- 官方公告措辭可能改變，因此解析規則仍應在真實天氣事件中持續驗證。

## Disclaimer

這是技術 PoC。惡劣天氣期間，仍應以香港特區政府、香港天文台、教育局及僱主正式指示為準。
