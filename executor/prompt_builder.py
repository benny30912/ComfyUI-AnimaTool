"""
雙模型提示詞拼接引擎。

根據 AI 輸出的語義結構，分別為 Anima 和 SDXL 生成各自的提示詞。
括號轉義交給工作流中的 PromptCleaningMaid 節點處理。
"""
from __future__ import annotations

from typing import Any, Dict, List

# ============================
# 固定常量
# ============================

# --- Anima 固定前綴 ---
ANIMA_QUALITY_PREFIX = (
    "masterpiece, best quality, newest, very aesthetic, absurdres, "
    "score_9, score_8, score_7"
)
ANIMA_NEGATIVE_PREFIX = (
    "worst quality, low quality, score_1, score_2, score_3, "
    "blurry, jpeg artifacts, sepia"
)

# --- SDXL 固定前綴 ---
SDXL_QUALITY_PREFIX = "masterpiece, best quality, amazing quality"
SDXL_NEGATIVE_PREFIX = "bad quality, worst quality, worst detail, sketch, censor"

# --- ResolutionSelector 枚舉 ---
ASPECT_RATIO_ENUM = [
    "1:1 (Square)",
    "3:2 (Photo)",
    "4:3 (Standard)",
    "16:9 (Widescreen)",
    "21:9 (Ultrawide)",
    "2:3 (Portrait Photo)",
    "3:4 (Portrait Standard)",
    "9:16 (Portrait Widescreen)",
]


# ============================
# 工具函數
# ============================

def _join_non_empty(*parts: str) -> str:
    """將非空部分以 ', ' 連接。"""
    return ", ".join(p for p in parts if p and p.strip())


def _normalize_artist(raw: str) -> List[str]:
    """
    將畫師字串拆分、清理。
    - 底線轉空格
    - 去除前後空白
    - 去除可能已有的 @ 前綴（統一後再加）
    """
    artists = []
    for a in raw.split(","):
        name = a.strip().lstrip("@").replace("_", " ").strip()
        if name:
            artists.append(name)
    return artists


# ============================
# Anima 拼接
# ============================

def build_anima_positive(params: Dict[str, Any]) -> str:
    """
    按 Anima 規範拼接正向提示詞。

    順序：[固定 quality/score] [safety] [人數] [角色] [作品]
          [畫師(@前綴)] [風格] [外觀] [標籤] [環境] [自然語言]

    括號不做轉義，交給 PromptCleaningMaid 節點處理。
    """
    parts: List[str] = [ANIMA_QUALITY_PREFIX]

    # Safety（可選）
    safety = (params.get("safety") or "").strip()
    if safety:
        parts.append(safety)

    # 人數
    count = (params.get("count") or "").strip()
    if count:
        parts.append(count)

    # 角色（不做轉義）
    character = (params.get("character") or "").strip()
    if character:
        parts.append(character)

    # 作品（不做轉義）
    series = (params.get("series") or "").strip()
    if series:
        parts.append(series)

    # 畫師 — 自動加 @ 前綴，底線轉空格
    raw_artist = (params.get("artist") or "").strip()
    if raw_artist:
        artists = _normalize_artist(raw_artist)
        if artists:
            parts.append(", ".join(f"@{name}" for name in artists))

    # 風格、外觀、標籤、環境、自然語言
    for field in ("style", "appearance", "tags", "environment", "nltags"):
        val = (params.get(field) or "").strip()
        if val:
            parts.append(val)

    return ", ".join(p for p in parts if p)


def build_anima_negative(params: Dict[str, Any]) -> str:
    """
    Anima 負向提示詞：固定前綴 + AI 自由撰寫的部分。
    """
    custom = (params.get("neg") or "").strip()
    if custom:
        return f"{ANIMA_NEGATIVE_PREFIX}, {custom}"
    return ANIMA_NEGATIVE_PREFIX


# ============================
# SDXL 拼接
# ============================

def build_sdxl_positive(params: Dict[str, Any]) -> str:
    """
    按 SDXL 規範拼接正向提示詞。

    與 Anima 的差異：
    - 不同的固定 quality 前綴
    - 畫師不加 @ 前綴
    - 不做括號轉義（同樣交給 PromptCleaningMaid）
    """
    parts: List[str] = [SDXL_QUALITY_PREFIX]

    # Safety（可選，若 AI 提供了則加入）
    safety = (params.get("safety") or "").strip()
    if safety:
        parts.append(safety)

    # 人數
    count = (params.get("count") or "").strip()
    if count:
        parts.append(count)

    # 角色（不做轉義）
    character = (params.get("character") or "").strip()
    if character:
        parts.append(character)

    # 作品
    series = (params.get("series") or "").strip()
    if series:
        parts.append(series)

    # 畫師 — 不加 @ 前綴，底線轉空格
    raw_artist = (params.get("artist") or "").strip()
    if raw_artist:
        artists = _normalize_artist(raw_artist)
        if artists:
            parts.append(", ".join(artists))

    # 風格、外觀、標籤、環境、自然語言
    for field in ("style", "appearance", "tags", "environment", "nltags"):
        val = (params.get(field) or "").strip()
        if val:
            parts.append(val)

    return ", ".join(p for p in parts if p)


def build_sdxl_negative(params: Dict[str, Any]) -> str:
    """
    SDXL 負向提示詞：固定前綴 + AI 自由撰寫的部分。
    """
    custom = (params.get("neg") or "").strip()
    if custom:
        return f"{SDXL_NEGATIVE_PREFIX}, {custom}"
    return SDXL_NEGATIVE_PREFIX
