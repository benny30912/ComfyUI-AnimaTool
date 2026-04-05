# 雙模型接力生成（Anima + SDXL）知識庫

> 本文件供開發者和進階用戶參考。此內容不會自動發送給 LLM — LLM 的指導來自 MCP Tool Schema 和 Cherry Studio 的 System Prompt。

## 架構概述

本工作流使用 **Anima（circlestone-labs/Anima）** 生成底圖，再由 **wai Illustrious SDXL** 進行精修（img2img, denoise=0.5），實現雙模型接力生成。

```
自然語言描述 → AI 語義結構 → executor 雙拼接引擎
  → Anima 生成底圖（10 steps, cfg=5）
  → SDXL 精修（25 steps, cfg=6, denoise=0.5）
  → 最終圖片
```

## 兩個模型的提示詞差異

| 差異點 | Anima | SDXL |
|--------|-------|------|
| 固定正面前綴 | `masterpiece, best quality, newest, very aesthetic, absurdres, score_9, score_8, score_7` | `masterpiece, best quality, amazing quality` |
| 固定負面前綴 | `worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts, sepia` | `bad quality, worst quality, worst detail, sketch, censor` |
| 畫師前綴 | 自動加 `@`（如 `@fkey`） | 直接使用（如 `fkey`） |
| Safety 標籤 | 若 AI 提供則加入 | 若 AI 提供則加入 |
| 括號轉義 | 由 PromptCleaningMaid 節點自動處理 | 由 PromptCleaningMaid 節點自動處理 |

## Cherry Studio 推薦 System Prompt

```
你是一位二次元插畫生成助手。當用戶要求生成或修改圖片時，呼叫 generate_dual_image 工具。

【延續式修改規則】
- 每次呼叫工具時，你必須回傳所有欄位的完整值，而非只回傳變更的部分
- 基於上次工具回傳的 params_used，結合用戶本次的要求，組合出完整的新參數
- 用戶說「加上和服」→ 在上次的 tags 基礎上增加 kimono，其餘欄位保持不變
- 用戶說「不要月亮」→ 在 neg 中加入 moon，其餘欄位保持不變
- 除非用戶明確表示「重新來」「換一個主題」，否則始終延續上次的完整參數

【提示詞要點】
- Quality / Score 標籤由系統自動填入，你無需填寫
- artist 名字直接用空格，不要用底線（例如寫 kawakami rokkaku）
- character 只放角色名，series 只放作品名，tags 不要包含角色名和作品名
- 括號不需要手動轉義，系統會自動處理
- 不要寫可從主詞推導的常識性廢話
- 家喻戶曉的角色可以省略 appearance

【多圖生成】
若用戶要求生成多張圖片（如「畫 3 張不同的」），使用 repeat 參數。

【普通聊天】
若用戶不是要求生圖，正常聊天即可。不需要回覆你具體生成了什麼提示詞，直接呼叫工具。
```

## Cherry Studio 上下文管理

- **助手設定 → 上下文數量**：建議 2~4 條
- MCP 工具回傳的 `params_used` 只包含語義欄位，不含 base64 / seed 等大體積數據
- 圖片以 `ImageContent` 格式傳輸，不佔文字 token

## 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `COMFYUI_URL` | ComfyUI 服務地址 | `http://127.0.0.1:8188` |
| `ANIMATOOL_DUAL_WORKFLOW` | 雙模型工作流路徑 | `executor` 目錄的 `Workflow_for_api.json` |
| `ANIMATOOL_DOWNLOAD_IMAGES` | 是否保存到本地 | `true` |
| `ANIMATOOL_TIMEOUT` | 生成超時（秒） | `600` |
