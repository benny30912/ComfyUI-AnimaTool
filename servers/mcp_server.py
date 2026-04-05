"""
Anima Tool MCP Server

让 Cursor/Claude 等支持 MCP 的客户端可以直接调用图像生成，并原生显示图片。

启动方式：
    python -m servers.mcp_server

或在 Cursor 配置中添加此 MCP Server。
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Sequence, Optional

# 确保能 import 上层 executor
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    CallToolResult,
    Annotations,
)

from executor import AnimaExecutor, AnimaToolConfig
from knowledge import kb_parser

kb_parser.build_kb_index()

# 创建 MCP Server
server = Server("anima-tool")

# 全局 executor（懒加载）
_executor: AnimaExecutor | None = None


def get_executor() -> AnimaExecutor:
    global _executor
    if _executor is None:
        _executor = AnimaExecutor(config=AnimaToolConfig())
    return _executor


# Tool Schema（从 tool_schema_universal.json 简化）
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt_hint": {
            "type": "string",
            "description": "可选：人类可读的简短需求摘要，仅用于日志回显。"
        },
        "aspect_ratio": {
            "type": "string",
            "description": "可选：长宽比，如 '16:9'、'9:16'、'1:1'。默认 1:1。",
            "enum": ["21:9", "2:1", "16:9", "16:10", "5:3", "3:2", "4:3", "1:1", "3:4", "2:3", "3:5", "10:16", "9:16", "1:2", "9:21"]
        },
        "width": {"type": "integer", "description": "可选：宽度（像素），须为16倍数。若指定则覆盖 aspect_ratio。"},
        "height": {"type": "integer", "description": "可选：高度（像素），须为16倍数。若指定则覆盖 aspect_ratio。"},
        "quality_meta_year_safe": {
            "type": "string",
            "description": "必选：质量/年份/安全标签。必须包含 safe/sensitive/nsfw/explicit 之一。示例: 'masterpiece, best quality, year 2024, safe'"
        },
        "count": {
            "type": "string",
            "description": "必选：人数标签，如 '1girl'、'2girls'、'1boy'。"
        },
        "character": {"type": "string", "description": "可选：角色名（可含作品名括号），如 'hatsune miku' 或 'yunli (honkai star rail)'。"},
        "series": {"type": "string", "description": "可选：作品/系列名，如 'vocaloid'。"},
        "appearance": {"type": "string", "description": "可选：角色固定外观描述（发色、眼睛、身材等，不含服装）。"},
        "artist": {
            "type": "string",
            "description": "必选：画师标签，必须以 @ 开头（如 @fkey）。多画师逗号分隔。若用户没指定画师，请根据风格推荐一位。"
        },
        "style": {"type": "string", "description": "可选：画风倾向或特定渲染风格。"},
        "tags": {
            "type": "string",
            "description": "必选：核心 Danbooru 标签（逗号分隔）。建议包含动作、构图、服装、表情等。"
        },
        "nltags": {"type": "string", "description": "可选：自然语言补充（仅在 tag 难以描述时使用）。"},
        "environment": {"type": "string", "description": "可选：环境与背景光影描述。"},
        "neg": {
            "type": "string",
            "description": "必选：负面提示词。默认已包含通用反咒。建议加入与安全标签相反的约束。",
            "default": "worst quality, low quality, score_1, score_2, score_3, blurry, bad hands, bad anatomy, text, watermark"
        },
        "steps": {"type": "integer", "description": "可选：步数，默认 25。", "default": 25},
        "cfg": {"type": "number", "description": "可选：CFG，默认 4.5。", "default": 4.5},
        "sampler_name": {"type": "string", "description": "可选：采样器，默认 er_sde。", "default": "er_sde"},
        "seed": {"type": "integer", "description": "可选：随机种子。不填则每次生成都随机。"},
        "repeat": {
            "type": "integer",
            "description": "可选：独立任务重复次数。每次都会有不同随机种子。默认 1。",
            "default": 1, "minimum": 1, "maximum": 16,
        },
        "batch_size": {
            "type": "integer",
            "description": "可选：单任务内的 batch size。默认 1。",
            "default": 1, "minimum": 1, "maximum": 4,
        },
        "loras": {
            "type": "array",
            "description": "可选：LoRA 列表。name 须匹配 list_anima_models(model_type=loras) 返回值。",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "LoRA 文件名（含子目录）"},
                    "weight": {"type": "number", "default": 1.0}
                },
                "required": ["name"]
            }
        },
        "unet_name": {
            "type": "string",
            "description": "高级：UNET 模型文件名，默认 Anima。用 list_anima_models(model_type=diffusion_models) 查询可用模型。",
        },
        "clip_name": {
            "type": "string",
            "description": "高级：文本编码器文件名，默认 Qwen3。用 list_anima_models(model_type=text_encoders) 查询。",
        },
        "vae_name": {
            "type": "string",
            "description": "高级：VAE 文件名，默认 Anima VAE。用 list_anima_models(model_type=vae) 查询。",
        },
    },
    "required": ["quality_meta_year_safe", "count", "artist", "tags", "neg"]
}


# ============================================================
# 雙模型接力生成 Tool Schema — Cherry Studio 唯一暴露的工具
# ============================================================
DUAL_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "base_node_id": {
            "type": "string",
            "description": "如果你是在修改先前生成的圖片，請傳入該次生成的 node_id。若不提供且非全新生成，預設繼承全域最新節點。"
        },
        "is_new_generation": {
            "type": "boolean",
            "description": "如果你是在開始一個全新的繪畫任務（而非修改上一張圖），請設為 true，系統會切斷歷史避免污染新圖。",
            "default": False,
        },
        "reasoning": {
            "type": "string",
            "description": (
                "必填。在填寫其他欄位之前，先逐步詳細分析："
                "1) 用戶的明確要求是什麼；"
                "2) base_node_id 是什麼；"
                "3) 參考資料中滿足用戶明確要求的標籤有哪些；"
                "4) 哪些標籤需要新增，為什麼；"
                "5) 哪些舊標籤與新指示衝突，需要移除；"
                "6) 哪些欄位需要更新（appearance/environment 等）。"
                "7) 哪些欄位需要跳過（以保持原狀）。"
            ),
        },
        "aspect_ratio": {
            "type": "string",
            "description": "長寬比。不提供則繼承上次值。",
            "enum": [
                "1:1 (Square)",
                "3:2 (Photo)",
                "4:3 (Standard)",
                "16:9 (Widescreen)",
                "21:9 (Ultrawide)",
                "2:3 (Portrait Photo)",
                "3:4 (Portrait Standard)",
                "9:16 (Portrait Widescreen)",
            ],
        },
        "safety": {
            "type": "string",
            "description": "安全標籤，基於畫面內容選擇。不提供則繼承上次值。",
            "enum": ["safe", "sensitive", "nsfw", "explicit"],
        },
        "count": {
            "type": "string",
            "description": "人數標籤，如 '1girl'、'2girls'、'1boy'、'no humans'。不提供則繼承上次值。",
        },
        "character": {
            "type": "string",
            "description": (
                "角色名（只放角色名，不放作品名）。"
                "如 'hatsune miku'、'serena (pokemon)'。不提供則繼承上次值。"
            ),
        },
        "series": {
            "type": "string",
            "description": (
                "作品/系列名（只放作品名，不放角色名）。"
                "如 'vocaloid'、'pokemon (game)'。不提供則繼承上次值。"
            ),
        },
        "artist": {
            "type": "string",
            "description": (
                "畫師名。名字中用空格而非底線。"
                "多畫師逗號分隔（建議只用 1 位以保持風格穩定）。不提供則繼承上次值。"
            ),
        },
        "appearance": {
            "type": "string",
            "description": (
                "角色固定外觀：髮型髮色、眼睛、身材等（不含服裝/飾品）。"
                "對於家喻戶曉的角色可省略。"
                "必須提供完整的最終值。不提供則繼承上次值。"
            ),
        },
        "style": {
            "type": "string",
            "description": (
                "畫風/渲染傾向。"
                "只在需要鎖定品類時才填（如 splash art / watercolor / pixel art），"
                "不要和 artist 風格衝突，不要寫互斥的風格詞。"
                "不提供則繼承上次值。"
            ),
        },
        "tags_add": {
            "type": "string",
            "description": (
                "本次要新增的 Danbooru 標籤（逗號分隔）。"
                "放動作/構圖/服裝/表情/鏡頭/身體部位。"
                "不要包含角色名和作品名，不要寫可從主詞推導的常識。"
                "總像素約 1MP，需保證主體占畫面比例大，避免細節模糊。"
                "會追加到現有標籤，不會覆蓋。"
            ),
        },
        "tags_remove": {
            "type": "string",
            "description": (
                "本次要移除的 Danbooru 標籤（逗號分隔）。"
                "與新指示衝突的舊標籤必須在此明確移除。"
            ),
        },
        "nltags": {
            "type": "string",
            "description": "用 1~2 句簡短的自然語言描述畫面或動作以補充 tag。必須提供完整值。不提供則繼承上次值。",
        },
        "environment": {
            "type": "string",
            "description": (
                "環境與光影描述。"
                "不要重複已可從其他標籤推導的常識"
                "（如寫了 beach 就不用寫 sand, water, sky）。"
                "不要重複已可從其他標籤推導的常識。必須提供完整的最終值。不提供則繼承上次值。"
            ),
        },
        "neg_add": {
            "type": "string",
            "description": (
                "本次要新增的負面提示詞（逗號分隔）。無須填寫品質提示詞。"
                "此處必須盡量填入防肢體崩壞的詞彙"
                "（如 anatomical nonsense, bad anatomy, bad hands, extra fingers, missing fingers, bad feet）"
                "以及絕對不能出現或需要依照使用者指示減少的元素。會追加到現有負面詞，不會覆蓋。"
            ),
        },
        "neg_remove": {
            "type": "string",
            "description": (
                "本次要移除的負面提示詞（逗號分隔）。"
                "用戶希望解除限制時使用。"
            ),
        },
        "repeat": {
            "type": "integer",
            "description": "生成張數。每張使用不同隨機種子。預設 1。",
            "default": 1,
            "minimum": 1,
            "maximum": 16,
        },
    },
    "required": [],
}

RANDOM_INSPIRATION_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "思考過程：為什麼要抽卡"
        },
        "categories": {
             "type": "array",
             "items": {"type": "string", "enum": ["artist", "character", "clothing", "scene", "all"]},
             "description": "要抽取的類別。預設 ['all'] 表示從所有類別各抽指定數量。"
        },
        "count": {
             "type": "integer",
             "description": "每個類別抽取的數量，預設 1",
             "default": 1
        }
    },
    "required": ["reasoning"]
}


LIST_MODELS_SCHEMA = {
    "type": "object",
    "properties": {
        "model_type": {
            "type": "string",
            "enum": ["loras", "diffusion_models", "vae", "text_encoders"],
            "description": "模型类型。loras 仅返回有 .json sidecar 元数据的 LoRA。",
        }
    },
    "required": ["model_type"],
}


LIST_HISTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "description": "返回最近几条历史记录（默认 5）",
            "default": 5, "minimum": 1, "maximum": 50,
        },
    },
}


# reroll schema：source 必填 + generate 的所有参数可作为【可选覆盖项】
# 关键：需要把原 generate 中"必选"的描述改为"可选覆盖"，否则 AI 会自动填入
def _build_reroll_override_props() -> dict:
    """复制 generate schema 的属性，但将描述中的'必选'改为'可选覆盖'。"""
    import copy
    props = copy.deepcopy(TOOL_SCHEMA["properties"])
    for _k, _v in props.items():
        desc = _v.get("description", "")
        if desc.startswith("必选："):
            _v["description"] = "可选覆盖（不提供则沿用历史记录）：" + desc[3:]
    return props


_REROLL_OVERRIDE_PROPS = _build_reroll_override_props()
REROLL_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {
            "type": "string",
            "description": "必选：要 reroll 的基础记录。'last' 表示最近一条，或使用历史 ID（如 '12'）。",
        },
        **_REROLL_OVERRIDE_PROPS,
    },
    "required": ["source"],
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    """列出可用工具"""
    return [
        Tool(
            name="get_node_params",
            description=(
                "查詢指定節點或最新節點生成的完整參數狀態和先前的圖片。"
                "如果上下文中能直接看到 params_used，請直接使用它，不要呼叫此工具。"
            ),
            inputSchema={"type": "object", "properties": {"node_id": {"type": "string", "description": "要查詢的節點 ID，不填默認最新"}}, "required": []},
        ),
        Tool(
            name="list_recent_nodes",
            description="查詢最近的節點歷史紀錄（樹狀結構），幫助回想起以前畫過的圖片對應的 node_id。",
            inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "description": "查詢數量，預設10"}}, "required": []},
        ),

        Tool(
            name="random_inspiration",
            description="從知識庫中隨機抽取畫師配方、角色、服裝、場景等元素。可用於「隨機抽卡」或激發靈感。",
            inputSchema=RANDOM_INSPIRATION_SCHEMA,
        ),
        Tool(
            name="generate_dual_image",
            description=(
                "使用 Anima + SDXL 雙模型接力生成二次元/插畫圖片。"
                "系統會自動處理畫師前綴、括號轉義、Quality/Score 標籤等格式差異。"
                "只需提供語義描述即可。"
            ),
            inputSchema=DUAL_TOOL_SCHEMA,
        ),
    ]


async def _generate_with_repeat(
    executor: "AnimaExecutor",
    prompt_json: Dict[str, Any],
) -> list[TextContent | ImageContent]:
    """执行单模型生成（支持 repeat），返回 MCP 内容列表。保留供舊工具使用。"""
    from copy import deepcopy

    repeat = max(1, int(prompt_json.pop("repeat", 1) or 1))

    all_contents: list[TextContent | ImageContent] = []
    history_ids: list[int] = []

    for i in range(repeat):
        run_params = deepcopy(prompt_json)
        if "seed" not in prompt_json or prompt_json.get("seed") is None:
            run_params.pop("seed", None)

        result = await asyncio.to_thread(executor.generate, run_params)

        if not result.get("success"):
            all_contents.append(TextContent(type="text", text=f"第 {i+1}/{repeat} 次生成失败: {result}"))
            continue

        if result.get("history_id"):
            history_ids.append(result["history_id"])

        for img in result.get("images", []):
            if img.get("base64") and img.get("mime_type"):
                all_contents.append(
                    ImageContent(
                        type="image",
                        data=img["base64"],
                        mimeType=img["mime_type"],
                    )
                )

    if not all_contents:
        all_contents.append(TextContent(type="text", text="生成完成，但没有产出图片。"))

    if history_ids:
        ids_str = ", ".join(f"#{hid}" for hid in history_ids)
        hint = f"已保存为历史记录 {ids_str}。可用 reroll_anima_image(source=\"{history_ids[-1]}\") 或 reroll_anima_image(source=\"last\") 重新生成。"
        all_contents.append(TextContent(type="text", text=hint))

    return all_contents


@dataclass
class NodeState:
    node_id: str
    parent_id: Optional[str]
    params: Dict[str, Any]
    images_markdown: str
    timestamp: float

_MAX_NODES = 200
_node_tree: OrderedDict[str, NodeState] = OrderedDict()
_global_last_node_id: Optional[str] = None
_last_generate_time: float = 0.0  # 上次生成的時間戳（用於冷卻防護）
_GENERATE_COOLDOWN: float = 5.0   # 冷卻時間（秒）

def _add_node(node: NodeState):
    global _global_last_node_id, _node_tree
    _node_tree[node.node_id] = node
    _global_last_node_id = node.node_id
    if len(_node_tree) > _MAX_NODES:
        _node_tree.popitem(last=False)


def _apply_tag_diff(current: str, add: str, remove: str) -> str:
    """對逗號分隔的標籤字串執行差量更新。"""
    # 解析現有標籤（保持順序）
    tags = [t.strip() for t in current.split(",") if t.strip()] if current else []

    # 移除指定標籤
    if remove:
        remove_set = {t.strip().lower() for t in remove.split(",") if t.strip()}
        tags = [t for t in tags if t.strip().lower() not in remove_set]

    # 追加新標籤（不重複）
    if add:
        existing_lower = {t.strip().lower() for t in tags}
        for t in add.split(","):
            t = t.strip()
            if t and t.lower() not in existing_lower:
                tags.append(t)
                existing_lower.add(t.lower())

    return ", ".join(tags)


def _merge_node_params(new_params: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[str]]:
    """
    將新參數與指定/上一次的參數合併。
    """
    global _global_last_node_id, _node_tree
    is_new = new_params.pop("is_new_generation", False)
    base_node_id = new_params.pop("base_node_id", None)
    
    parent_id = None
    merged = {}
    
    if not is_new:
        if base_node_id and base_node_id in _node_tree:
            parent_id = base_node_id
            merged = dict(_node_tree[base_node_id].params)
        elif _global_last_node_id and _global_last_node_id in _node_tree:
            parent_id = _global_last_node_id
            merged = dict(_node_tree[_global_last_node_id].params)

    # 需要完整覆蓋的欄位（提供了就用新值）
    _FULL_FIELDS = (
        "aspect_ratio", "safety", "count", "character", "series",
        "artist", "appearance", "style", "nltags", "environment",
    )
    for field in _FULL_FIELDS:
        if field in new_params and new_params[field] is not None:
            val = str(new_params[field]).strip()
            if val.upper() == "CLEAR":
                merged[field] = ""
            elif val:
                merged[field] = val

    # tags 差量更新
    current_tags = merged.get("tags", "")
    tags_add = (new_params.get("tags_add") or "").strip()
    tags_remove = (new_params.get("tags_remove") or "").strip()
    if tags_add or tags_remove:
        merged["tags"] = _apply_tag_diff(current_tags, tags_add, tags_remove)

    # neg 差量更新
    current_neg = merged.get("neg", "")
    neg_add = (new_params.get("neg_add") or "").strip()
    neg_remove = (new_params.get("neg_remove") or "").strip()
    if neg_add or neg_remove:
        merged["neg"] = _apply_tag_diff(current_neg, neg_add, neg_remove)

    return merged, parent_id


async def _generate_dual_with_repeat(
    executor: "AnimaExecutor",
    params: Dict[str, Any],
) -> list[TextContent | ImageContent]:
    global _last_generate_time, _node_tree
    from copy import deepcopy

    repeat = max(1, int(params.pop("repeat", 1) or 1))

    merged, parent_id = _merge_node_params(params)

    image_urls: list[str] = []
    errors: list[str] = []

    for i in range(repeat):
        run_params = deepcopy(merged)
        result = await asyncio.to_thread(executor.generate_dual, run_params)

        if not result.get("success"):
            errors.append(f"第 {i+1}/{repeat} 次生成失敗")
            continue

        for img in result.get("images", []):
            url = img.get("view_url") or img.get("url")
            if url:
                image_urls.append(url)

    contents: list[TextContent] = []
    new_node_ids = []

    if image_urls:
        md = "\n".join(f"![]({url})" for url in image_urls)
        new_node_id = str(uuid.uuid4())[:6]
        new_node = NodeState(
            node_id=new_node_id,
            parent_id=parent_id,
            params=merged,
            images_markdown=md,
            timestamp=time.time()
        )
        _add_node(new_node)
        new_node_ids.append(new_node_id)
        
        contents.append(TextContent(
            type="text",
            text=f"{md}\nNode ID: {new_node_id}",
        ))

    if errors:
        contents.append(TextContent(
            type="text",
            text=json.dumps({"errors": errors}, ensure_ascii=False),
        ))

    _SEMANTIC_FIELDS = (
        "aspect_ratio", "safety", "count", "character", "series",
        "artist", "appearance", "style", "tags", "nltags",
        "environment", "neg",
    )
    params_used = {k: v for k, v in merged.items()
                   if k in _SEMANTIC_FIELDS and v}
    if new_node_ids:
        params_used["node_id"] = new_node_ids[0]
        
    if params_used:
        contents.append(TextContent(
            type="text",
            text=json.dumps({"params_used": params_used}, ensure_ascii=False),
        ))

    if not contents:
        contents.append(TextContent(type="text", text="生成完成，但沒有產出圖片。"))

    return contents


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> Sequence[TextContent | ImageContent]:
    """调用工具"""
    try:
        global _last_generate_time, _node_tree, _global_last_node_id
        args = dict(arguments or {})

        # ---- get_node_params ----
        if name == "get_node_params":
            node_id = str(args.get("node_id", "")).strip()
            target_id = node_id if node_id else _global_last_node_id
            if not target_id or target_id not in _node_tree:
                return [TextContent(type="text", text="伺服器無歷史參數或找不到指定節點。")]
            node = _node_tree[target_id]
            _SEMANTIC_FIELDS = (
                "aspect_ratio", "safety", "count", "character", "series",
                "artist", "appearance", "style", "tags", "nltags",
                "environment", "neg",
            )
            current = {k: v for k, v in node.params.items() if k in _SEMANTIC_FIELDS and v}
            result: dict = {"current_params": current, "node_id": node.node_id, "parent_id": node.parent_id}
            if node.images_markdown:
                result["last_images_markdown"] = node.images_markdown
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

        # ---- list_recent_nodes ----
        if name == "list_recent_nodes":
            limit = int(args.get("limit", 10))
            nodes = list(_node_tree.values())[-limit:]
            if not nodes:
                return [TextContent(type="text", text="暫無節點歷史。")]
            lines = []
            for n in reversed(nodes):
                char = n.params.get('character', '') or 'N/A'
                tags = n.params.get('tags', '')[:30]
                lines.append(f"Node [{n.node_id}] (Parent: {n.parent_id}) - Char: {char}, Tags: {tags}...")
            return [TextContent(type="text", text="\n".join(lines))]


        # ---- random_inspiration ----
        if name == "random_inspiration":
            categories = args.get("categories", ["all"])
            count = int(args.get("count", 1))
            res = kb_parser.draw_random(categories, count)
            return [TextContent(type="text", text=json.dumps({"inspiration": res}, ensure_ascii=False))]

        executor = get_executor()

        # ---- list_anima_models ----
        if name == "list_anima_models":
            model_type = str(args.get("model_type") or "").strip()
            if not model_type:
                return [TextContent(type="text", text="参数错误：model_type 不能为空")]
            result = await asyncio.to_thread(executor.list_models, model_type)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

        # ---- list_anima_history ----
        if name == "list_anima_history":
            limit = int(args.get("limit") or 5)
            records = executor.history.list_recent(limit)
            if not records:
                return [TextContent(type="text", text="暂无生成历史。")]
            lines = [r.summary() for r in records]
            return [TextContent(type="text", text="\n".join(lines))]

        # ---- reroll_anima_image ----
        if name == "reroll_anima_image":
            source = str(args.pop("source", "")).strip()
            if not source:
                return [TextContent(type="text", text="参数错误：source 不能为空（使用 'last' 或历史 ID）")]

            record = executor.history.get(source)
            if record is None:
                return [TextContent(type="text", text=f"未找到历史记录：{source}。请先使用 list_anima_history 查看可用记录。")]

            # 深拷贝原始参数，用覆盖项更新
            from copy import deepcopy
            merged = deepcopy(record.params)
            overrides = {k: v for k, v in args.items() if v is not None}
            merged.update(overrides)

            # seed 默认行为：未显式指定则自动随机（删掉原 seed）
            if "seed" not in args or args.get("seed") is None:
                merged.pop("seed", None)

            return await _generate_with_repeat(executor, merged)

        # ---- generate_dual_image ----
        if name == "generate_dual_image":
            # 處理新建圖標記移至 _merge_node_params 內
                
            # 冷卻防護：攔截同一輪 LLM 的重複呼叫
            # 時間戳在生成**完成後**記錄，所以冷卻窗口從完成時刻開始計算
            now = time.time()
            if now - _last_generate_time < _GENERATE_COOLDOWN:
                return [TextContent(
                    type="text",
                    text="⚠️ 生成請求被攔截：距離上次生成完成不足 5 秒。"
                         "若需生成多張請使用 repeat 參數，而非重複呼叫此工具。",
                )]
            result = await _generate_dual_with_repeat(executor, args)
            _last_generate_time = time.time()  # 完成後記錄
            return result

        # ---- generate_anima_image（舊工具，保留但未註冊） ----
        if name == "generate_anima_image":
            return await _generate_with_repeat(executor, args)

        return [TextContent(type="text", text=f"未知工具: {name}")]

    except Exception as e:
        import traceback
        traceback.print_exc()
        return [TextContent(type="text", text=f"错误: {str(e)}")]


async def main():
    """启动 MCP Server（stdio 模式）"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
