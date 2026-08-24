# 香港惡劣天氣／停課 WhatsApp 通知

英文版：[README.md](README.md)

這是一個部署在 Google Cloud Run 的自動通知服務，會定期檢查香港官方公開資訊，只有在出現真正需要注意的新事件時，才透過 CallMeBot 發送真人可讀的 WhatsApp 通知。

## 系統架構

```text
Cloud Scheduler（每分鐘）
        ↓
Cloud Run /check
        ↓
官方來源彼此獨立檢查
        ├─ 香港天文台警告摘要
        ├─ 香港天文台特別天氣提示
        ├─ 香港教育局 RSS
        └─ 香港政府新聞公報 RSS
        ↓
Firestore 永久狀態 + 去重
        ↓
只有新的重要事件
        ↓
收件人
        ├─ Google Sheet（多人）
        └─ 環境變數（單人 fallback）
        ↓
CallMeBot → WhatsApp
```

## 官方來源

| 官方來源 | 用途 |
|---|---|
| 香港天文台 `warnsum` | 熱帶氣旋與暴雨警告狀態 |
| 香港天文台 `swt` | Pre-No. 8／特別天氣提示 |
| 香港教育局 RSS | 正式停課公告 |
| 香港政府新聞公報 RSS | 極端情況與政府特別公告 |

上述香港政府來源都不需要 API Key。

## 什麼狀態會通知？

Cloud Scheduler 可以每分鐘呼叫 `/check`，但 **不代表每分鐘都發 WhatsApp**。只有偵測到「新的、需要通知的狀態」，而且該狀態之前尚未成功通知過，才會呼叫 CallMeBot。

| 官方狀態／訊息 | 系統狀態 | 會發 WhatsApp？ | 說明 |
|---|---|---:|---|
| 一號戒備信號 | `T1` | 否 | 只記錄作為前一狀態 |
| 三號強風信號 | `T3` | 否 | 只記錄作為前一狀態 |
| 天文台預告可能發出八號信號 | `PRE_T8` | **是** | 來自 HKO Special Weather Tips |
| 八號烈風或暴風信號 | `T8` | **是** | 重要通知 |
| 九號烈風或暴風風力增強信號 | `T9` | **是** | 重要通知 |
| 十號颶風信號 | `T10` | **是** | 重要通知 |
| 黃色暴雨警告 | `AMBER_RAIN` | 否 | 只記錄，不主動通知 |
| 紅色暴雨警告 | `RED_RAIN` | **是** | 重要通知 |
| 黑色暴雨警告 | `BLACK_RAIN` | **是** | 重要通知 |
| 極端情況 | `EXTREME_CONDITIONS` | **是** | 政府特別安排 |
| 教育局正式停課 | `SUSPENDED` | **是** | 正式停課公告 |
| 酷熱天氣警告 `WHOT` | 不轉成通知事件 | 否 | HKO 來源仍會正常讀取，但目前不在停班／停課通知範圍 |
| 寒冷天氣、雷暴、強烈季候風等其他 HKO 警告 | 不轉成通知事件 | 否 | 目前不在通知範圍 |

例如：

```text
10:30 T3        → 記錄，不通知
11:00 Pre-T8    → WhatsApp 通知一次
11:01 Pre-T8    → 不重複
12:00 T8        → WhatsApp 通知一次
12:01 T8        → 不重複
```

## 某一個官方來源失敗時怎麼處理？

各來源彼此獨立，不會因為其中一個來源失敗，就阻擋其他正常來源的有效通知。

例如：

```text
教育局 RSS 暫時失敗
香港天文台正常偵測到新的 T8
→ T8 仍然會照常通知 CallMeBot
```

失敗的來源會保留先前狀態，不會因為暫時抓不到就被誤判成「事件解除」。等該來源恢復正常後再重新判斷。

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

## Google Sheet 多人收件人

如果是少量多人使用，Google Sheet 就是給非技術使用者維護的收件人清單。

請建立一個工作表 tab，名稱必須是：

```text
Recipients
```

只保留這三欄：

| name | phone | api_key |
|---|---|---|
| Jackie | 8869XXXXXXXX | XXXXXXX |
| Amy | 8529XXXXXXX | XXXXXXX |

規則：

- 只要一列同時有 `phone` 與 `api_key`，就視為啟用。
- 新增收件人：新增一列。
- 停止某人收件：直接刪掉該列。
- 每一位收件人都必須各自完成 CallMeBot 啟用，並使用自己的 API key。
- 因為 Sheet 中有 API key，請限制 Sheet 的分享權限，不要設成公開連結可存取。

目前使用的 Google Sheet ID：

```text
1IA5MgL130nSUvhCan2NIhIv7bpxZU1hNK4AChUjMEBI
```

使用範圍：

```text
Recipients!A:C
```

### 讓 Cloud Run 自動讀這張 Sheet

先找出 Cloud Run runtime service account：

```bash
PROJECT_NUMBER=$(gcloud projects describe hk-weather-whatsapp-poc --format='value(projectNumber)')
echo "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
```

把輸出的 service account email 加到 Google Sheet 的「共用」名單，權限只需要 **檢視者 Viewer**。

這樣 Cloud Run 之後就能自己讀取 Sheet，不需要你的個人 Google 帳號保持登入。

啟用 Sheets API：

```bash
gcloud services enable sheets.googleapis.com
```

再設定 Cloud Run：

```bash
gcloud run services update hk-weather-whatsapp-poc \
  --region asia-east2 \
  --update-env-vars RECIPIENTS_SHEET_ID=1IA5MgL130nSUvhCan2NIhIv7bpxZU1hNK4AChUjMEBI,RECIPIENTS_SHEET_RANGE=Recipients!A:C,DRY_RUN=false
```

只要設定了 `RECIPIENTS_SHEET_ID`，系統就會優先使用 Google Sheet 收件人，不再以單一 `CALLMEBOT_PHONE` / `CALLMEBOT_API_KEY` 作為主要收件人來源。

## API 端點

| Method | Endpoint | 用途 |
|---|---|---|
| GET | `/` | 服務基本資訊 |
| GET | `/health` | 版本、state backend、recipient mode |
| GET | `/sources` | 個別確認各官方來源是否成功 |
| POST | `/check` | 正式檢查並依規則決定是否通知 |
| POST | `/test-notification` | 測試目前設定的收件人 |

確認來源：

```bash
curl "$SERVICE_URL/sources"
```

`events_found: 0` 表示來源讀取成功，只是目前沒有符合通知條件的事件，不代表來源失敗。

## Google Cloud 部署／升級

取得最新程式：

```bash
cd ~/hk-weather-whatsapp-poc
git pull
```

啟用必要 API：

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com cloudscheduler.googleapis.com sheets.googleapis.com
```

既有 Cloud Run 服務請使用 `--update-env-vars`，避免清掉原本環境變數：

```bash
gcloud run deploy hk-weather-whatsapp-poc \
  --source . \
  --region asia-east2 \
  --allow-unauthenticated \
  --max-instances=1 \
  --concurrency=1 \
  --update-env-vars STATE_BACKEND=firestore,BOOTSTRAP_SILENT=true
```

取得 URL：

```bash
SERVICE_URL=$(gcloud run services describe hk-weather-whatsapp-poc --region asia-east2 --format='value(status.url)')
echo "$SERVICE_URL"
```

驗證：

```bash
curl "$SERVICE_URL/health"
curl "$SERVICE_URL/sources"
curl -X POST "$SERVICE_URL/check"
```

## Firestore 防重複通知

Firestore 會保存目前狀態與已成功通知的狀態，因此 Cloud Run restart、scale to zero 或換 instance 後，不會因為忘記舊狀態而重複通知。

當某個來源正常，而且之前的事件已經消失時，系統會靜默把該事件重設為 inactive，不另外發「解除」訊息。這樣未來下一次新的 T8／停課事件仍可再次正常通知。

## Cloud Scheduler

每分鐘檢查：

```bash
gcloud scheduler jobs create http hk-weather-check \
  --location=asia-east2 \
  --schedule="* * * * *" \
  --time-zone="Asia/Hong_Kong" \
  --uri="$SERVICE_URL/check" \
  --http-method=POST
```

提醒：**每分鐘執行 `/check`，不代表每分鐘發 WhatsApp。**

## 尚有限制

- 目前事件解除會靜默重設，尚未另外發送 `✅ 警告已解除` WhatsApp。
- CallMeBot 適合個人／少量多人 PoC，不適合作為企業大量群發服務。
- 官方公告措辭可能改變，因此解析規則仍應在真實惡劣天氣事件中持續驗證。

## Disclaimer

這是技術 PoC。惡劣天氣期間，仍應以香港特區政府、香港天文台、教育局及僱主正式指示為準。
