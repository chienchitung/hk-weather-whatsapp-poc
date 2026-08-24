# 香港惡劣天氣／停課 WhatsApp 通知

英文版：[README.md](README.md)

這是一個部署在 Google Cloud Run 的自動通知服務，會定期檢查香港官方公開資訊，只有在出現真正需要注意的新事件時，才透過 CallMeBot 發送真人可讀的 WhatsApp 通知。

## 系統架構

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
Google Sheet 收件人
      ↓
CallMeBot → WhatsApp
```

某一個官方來源失敗，不會阻擋其他健康來源的有效事件通知；失敗來源只會保留原本狀態，等來源恢復後再重新判斷。

## 哪些狀態會通知？

| 官方狀態 | 系統狀態 | WhatsApp |
|---|---|---|
| 一號戒備信號 | `T1` | 不通知 |
| 三號強風信號 | `T3` | 不通知 |
| 預告可能發出八號 | `PRE_T8` | **通知** |
| 八號風球 | `T8` | **通知** |
| 九號風球 | `T9` | **通知** |
| 十號風球 | `T10` | **通知** |
| 黃色暴雨 | `AMBER_RAIN` | 不通知 |
| 紅色暴雨 | `RED_RAIN` | **通知** |
| 黑色暴雨 | `BLACK_RAIN` | **通知** |
| 極端情況 | `EXTREME_CONDITIONS` | **通知** |
| 教育局停課 | `SUSPENDED` | **通知** |
| 酷熱天氣警告 `WHOT` | 不轉成事件 | 不通知 |
| 寒冷／雷暴／季候風等其他警告 | 不轉成事件 | 不通知 |

Cloud Scheduler 可以每分鐘呼叫 `/check`，但只有「新的、在通知白名單內、而且尚未通知過」的狀態才會呼叫 CallMeBot。

## Google Sheet 收件人

`Recipients` 工作表只需要三欄：

```text
name | phone | api_key
```

例如：

```text
Jackie | 8869XXXXXXXX | XXXXXXX
Amy    | 8529XXXXXXX  | XXXXXXX
```

只要一列同時有 `phone` 與 `api_key`，就視為有效收件人。新增使用者就新增一列；移除使用者就刪掉該列。

目前設定：

```text
RECIPIENTS_SHEET_ID=1IA5MgL130nSUvhCan2NIhIv7bpxZU1hNK4AChUjMEBI
RECIPIENTS_SHEET_RANGE=Recipients!A:C
```

Google Sheet 必須以「檢視者」分享給 Cloud Run runtime service account。

## v0.4.1：新人單獨測試通知

新增一個人到 Sheet 後，不需要等待真的 T8 或停課事件才知道設定是否成功。

只測某一位：

```bash
curl -X POST "$SERVICE_URL/test-recipient?name=Amy"
```

這個 endpoint 只會傳給 Amy，不會傳給其他人。對方收到的內容會清楚標示為「設定測試」，不是正式惡劣天氣警告。

成功後，Firestore 會在以下 collection 保存驗證紀錄：

```text
hk_weather_recipient_status
```

例如：

```text
name: Amy
verified: true
last_test_status: success
last_test_at: 2026-08-24...
```

如果 CallMeBot 尚未正常授權或傳送失敗，會記錄：

```text
verified: false
last_test_status: failed
```

**verified 只是管理用狀態，不是正式通知的阻擋條件。** 只要使用者仍存在 Google Sheet，真的出現 T8、黑雨、停課等事件時，`/check` 還是會嘗試傳送。

### 查看所有收件人的驗證狀態

```bash
curl "$SERVICE_URL/recipients"
```

範例：

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

這個 endpoint 不會回傳 phone 或 api_key。

`POST /test-notification` 則保留為「一次測試 Sheet 裡所有收件人」。

## API 端點

| Method | Endpoint | 用途 |
|---|---|---|
| GET | `/` | 服務資訊 |
| GET | `/health` | 版本、state backend、收件人模式 |
| GET | `/sources` | 個別確認所有官方來源 |
| GET | `/recipients` | 查看收件人驗證狀態 |
| POST | `/check` | 正式檢查並依規則決定是否通知 |
| POST | `/test-notification` | 測試全部收件人 |
| POST | `/test-recipient?name=...` | 只測一位收件人 |

## 升級到 v0.4.1

```bash
cd ~/hk-weather-whatsapp-poc
git pull
```

確認：

```bash
grep 'version=' app.py
```

應看到：

```text
version="0.4.1"
```

重新部署既有 Cloud Run service：

```bash
gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated \
  --max-instances=1 \
  --concurrency=1 \
  --update-env-vars 'STATE_BACKEND=firestore,RECIPIENTS_SHEET_ID=1IA5MgL130nSUvhCan2NIhIv7bpxZU1hNK4AChUjMEBI,RECIPIENTS_SHEET_RANGE=Recipients!A:C,DRY_RUN=false'
```

取得 URL：

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc \
  --region asia-east2 \
  --format='value(status.url)')
```

驗證：

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl "$SERVICE_URL/recipients"
```

`/health` 應看到：

```json
{
  "status":"ok",
  "version":"0.4.1",
  "state_backend":"firestore",
  "recipient_mode":"google_sheet"
}
```

## 新增使用者的標準流程

```text
1. 新人在 WhatsApp 完成 CallMeBot activation
2. 取得自己的 API key
3. 在 Google Sheet 新增 name / phone / api_key
4. 管理者執行 /test-recipient?name=新人姓名
5. 新人收到「設定完成」測試訊息
6. /recipients 顯示 verified=true
7. 完成
```

姓名在 Sheet 中最好保持唯一，因為 `/test-recipient?name=...` 是用姓名找人；如果有兩列同名，endpoint 會回 409，避免測錯人。

## Cloud Scheduler

如果尚未建立：

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

已存在則更新：

```bash
gcloud scheduler jobs update http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

手動測試：

```bash
gcloud scheduler jobs run hk-weather-check --location=asia-east2
```

## 狀態與去重

- Firestore 保存天氣通知 state，Cloud Run restart 或 scale to zero 不會忘記已通知狀態。
- T1、T3、黃雨只做背景狀態，不發通知。
- 健康來源不再回報某事件時，系統會靜默清除該事件 state，讓下一次新的事件可以再次通知。
- 某來源失敗時，不會把該來源之前的事件誤判成解除，也不會阻擋其他來源。
- 收件人驗證資訊另外存在 `hk_weather_recipient_status`，不混入 Google Sheet。

## 注意

Google Sheet 內含 CallMeBot API key，因此不要設為公開或「知道連結的人都可以查看」。只分享給需要維護的人與 Cloud Run service account。

## Disclaimer

這是技術 PoC。惡劣天氣期間，仍應以香港特區政府、香港天文台、教育局及僱主正式指示為準。
