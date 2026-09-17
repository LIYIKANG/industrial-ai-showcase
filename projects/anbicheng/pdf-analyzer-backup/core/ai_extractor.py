"""
core/ai_extractor.py
====================
Claude AI 字段提取逻辑：
  - extract_from_single_pdf()  →  从 PDF（文字层或扫描件）提取字段
  - extract_from_image()       →  从图片文件提取字段

两个函数均返回 (values_dict, notes_dict)。
"""

import asyncio
import base64
import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anthropic

from .config import CLAUDE_MODEL, claude_client
from .pdf_utils import extract_text_from_pdf_bytes, pdf_bytes_to_base64_images

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

# 支持的图片扩展名
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# 扩展名 → MIME 类型映射
_MEDIA_TYPE_MAP: Dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

# AI 输出格式说明（日文提示，保留原语义）
_NOTE_FIELD_INSTRUCTION = (
    "JSON の各キーはフィールドの id、値は2つの属性を持つオブジェクト:\n"
    "  \"value\": 文書に確実に存在し、実際に読み取れた値のみを記入する。\n"
    "    - 文書にその情報が無い場合は、必ず空文字列を返す。\n"
    "    - 推測・常識・他フィールド（契約期間・入居日等）からの推断で、文書に無い値を捏造することを厳禁する。\n"
    "    - 見つからなければ空のままにし、埋めるために推測しない。\n"
    "  \"note\": 値が文書に存在するが、スキャンの不鮮明さ等で読み取りが不確実な場合のみ簡潔に説明する"
    "（例:「スキャン品質が低いため一部不鮮明」）。鮮明・確実な場合や値が空の場合は空文字列とする。\n"
    "    - value が空または推測値のときに note へ「推定」と書いて捏造値を隠すことを厳禁する。\n"
    "余分な自然言語の説明やコードブロック記号は一切出力しない。"
)

# 结构化字段规则说明。字段定义中没有这些键时可忽略，保持后方兼容。
_STRUCTURED_FIELD_RULE_INSTRUCTION = (
    "\n【フィールド定義の読み方】\n"
    "抽出するフィールド定義に追加キーがある場合は、次の意味として厳守してください：\n"
    "- description: そのフィールドが何を表すか。\n"
    "- source_hint: 文書内で優先して確認する欄・見出し・位置。\n"
    "- not_allowed: そのフィールドへ入れてはいけない値・役割・欄名。\n"
    "- format: 返す値の形式。単位や住所/氏名の混在を避ける指示を含む。\n"
    "- examples: 紛らわしい記載がある場合の選び方の例。\n"
    "label と description/source_hint/not_allowed/format が衝突する場合は、"
    "より具体的な description/source_hint/not_allowed/format/examples を優先してください。\n"
)

_SETTLEMENT_AMOUNT_INSTRUCTION = (
    "\n【精算書の金額ルール】\n"
    "- 金額フィールドは、実際に請求・精算される最終金額を返してください。\n"
    "- 表に「本体金額」「消費税」「合計金額」がある場合は、必ず「合計金額」を使用してください。\n"
    "- 「本体金額」だけを返してはいけません。\n"
    "- 「消費税」だけを返してはいけません。\n"
    "- 表に「精算金額」「請求金額」「合計金額」がある場合は、それを優先してください。\n"
    "- 明確に0円と記載されている場合は \"0\" を返してください。\n"
    "- 項目自体が無い場合は空文字を返してください。\n"
)

# 不動産契約書類向け テーブル読み取り精度向上の指示
_TABLE_READING_INSTRUCTION = (
    "\n【重要：テーブル・表の読み取りルール】\n"
    "この文書には賃貸決済明細書・契約金明細書・精算書などの表形式ページが含まれる可能性があります。\n"
    "表を読み取る際は以下を厳守してください：\n"
    "1. 各行の「項目名」と「金額」を正確に対応させること。隣接する行の金額を混同しないこと。\n"
    "   例：「鍵交換代金 29,700」と「駐輪場代金 1,000」を取り違えない。\n"
    "2. 「日割」金額（当月の日割計算額）と「月額」金額を区別すること。\n"
    "   - 賃料・共益費は「月額」を使用すること（日割の少額を使わない）。\n"
    "   - 「当月賃料」の日割額ではなく、契約書の月額賃料を使用すること。\n"
    "3. 物件所在地は「物件」「所在地」欄から読み取ること。\n"
    "   不動産会社の本社所在地と混同しないこと。\n"
    "4. 金額の桁数を正確に読むこと（100,000 を 10,000 と読み間違えない）。\n"
    "5. 敷金が0円または記載なしの場合は空文字を返すこと。\n"
    "6. 複数ページに同じ項目がある場合、賃貸決済明細書・契約金明細書の「精算金額」列を優先すること。\n"
)

# ── Claude API 呼び出し制御 ──────────────────────────────────────────────────
#
# [B] セマフォ: 同時 Claude API 呼び出し数を上限 CLAUDE_CONCURRENCY に制限。
#     Anthropic の TPM (tokens/min) レート制限は組織単位で共有されるため、
#     並列リクエストを増やすほど 429 が発生しやすくなる。デフォルト 2。
#
# [A] リトライ: 429 発生時は指数バックオフ + ジッター で最大 3 回再試行。
#     Retry-After ヘッダーが返された場合はそちらを優先する。

_CLAUDE_CONCURRENCY: int = int(os.getenv("CLAUDE_CONCURRENCY", "2"))
_MAX_RETRIES: int = 3
_RETRY_BASE_WAIT: float = 2.0   # 初回待機秒数（以降 ×2 ずつ増加: 2, 4, 8 秒）

_claude_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    """セマフォのレイジー初期化（イベントループ起動後に生成）。"""
    global _claude_semaphore
    if _claude_semaphore is None:
        _claude_semaphore = asyncio.Semaphore(_CLAUDE_CONCURRENCY)
    return _claude_semaphore


def _parse_retry_after(error: anthropic.RateLimitError) -> Optional[float]:
    """Retry-After ヘッダーの値を秒数として返す。取得できなければ None。"""
    try:
        val = error.response.headers.get("retry-after", "")
        return float(val) if val else None
    except Exception:
        return None


# ── 内部工具函数 ──────────────────────────────────────────────────────────────


def _parse_json_from_model_output(text: str) -> Dict[str, Any]:
    """解析模型输出，兼容 ```json``` 包裹格式和裸 JSON。

    增强容错：处理尾部多余逗号、截断的 JSON 等常见 AI 输出问题。
    """
    if not text or not text.strip():
        logger.warning("AI returned empty output")
        return {}

    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 尝试修复常见问题：尾部多余逗号
    fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 退化：截取第一个 { 到最后一个 }
    match = re.search(r"\{.*", cleaned, re.DOTALL)
    if match:
        raw = match.group(0)
        # 修复尾部逗号
        raw_fixed = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            return json.loads(raw_fixed)
        except json.JSONDecodeError:
            pass

        # 截断修复：JSON 可能被 max_tokens 截断，末尾不完整
        # 策略：从末尾向前找最后一个完整的 key-value 对（以 } 结尾）
        # 然后补上外层 }
        for i in range(len(raw_fixed) - 1, 0, -1):
            if raw_fixed[i] == "}":
                candidate = raw_fixed[: i + 1]
                # 检查是否缺少外层 }
                open_count = candidate.count("{")
                close_count = candidate.count("}")
                if open_count > close_count:
                    candidate += "}" * (open_count - close_count)
                # 修复末尾可能的多余逗号
                candidate = re.sub(r",\s*}", "}", candidate)
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue

    logger.error("Failed to parse AI JSON output. First 500 chars: %s", text[:500])
    return {}


def _parse_field_entry(raw_val: Any) -> Tuple[str, str]:
    """解析单个字段结果，返回 (value, note)。

    兼容旧格式（纯字符串）和新格式（含 note 的对象）。
    """
    if isinstance(raw_val, dict):
        value = str(raw_val.get("value", "") or "")
        note = str(raw_val.get("note", "") or "")
        return value, note
    return str(raw_val or ""), ""


async def _call_claude(system: str, messages: list, max_tokens: int = 4000) -> str:
    """Claude API を呼び出す。セマフォ制限 + 429 リトライ付き。

    [B] async with sem: 同時実行数を _CLAUDE_CONCURRENCY 以下に制限。
        セマフォはリトライ待機中も保持し、回復前に別リクエストが
        殺到して再び 429 を起こすのを防ぐ。

    [A] RateLimitError 時: Retry-After ヘッダー優先、なければ指数バックオフ
        (2→4→8 秒) + ランダムジッター(0〜1秒)で最大 3 回リトライ。
        429 以外の例外は即再送出し、呼び出し元でハンドリングさせる。
    """
    async with _get_semaphore():
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await claude_client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                )
                # content から text ブロックを抽出（先頭が text 以外でも安全に取得）
                return next((b.text for b in resp.content if b.type == "text"), "")

            except anthropic.RateLimitError as exc:
                if attempt == _MAX_RETRIES:
                    logger.error(
                        "Claude rate limit: %d回リトライ後も 429。リクエストを失敗扱いにします。",
                        _MAX_RETRIES,
                    )
                    raise
                wait = (_parse_retry_after(exc) or _RETRY_BASE_WAIT * (2 ** attempt))
                wait += random.uniform(0.0, 1.0)   # ジッター（同時リトライの衝突回避）
                logger.warning(
                    "Claude 429 rate limit (attempt %d/%d) → %.1f秒待機して再試行",
                    attempt + 1, _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)

    # ── 上記ループは必ず return か raise で終わる。ここには到達しない。
    raise RuntimeError("_call_claude: 予期しない制御フロー")


def _parse_raw_result(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """将模型原始 JSON 拆分为 values 和 notes 两个字典。"""
    values: Dict[str, Any] = {}
    notes: Dict[str, str] = {}
    for k, v in raw.items():
        values[k], notes[k] = _parse_field_entry(v)
    return values, notes


def _parse_line_items_result(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 VLM JSON 中取出 line_items 数组。"""
    if not isinstance(raw, dict):
        return []
    items = raw.get("line_items", raw.get("items", []))
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


_LINE_ITEM_VLM_INSTRUCTION = (
    "你是严谨的采购订单/报价单/产品价格表格识别助手。请只根据图片中实际看到的表格内容抽取 SKU 与价格。\n"
    "要求：\n"
    "1. 以页面图片为主进行识别，文字层仅作为辅助，不要因为文字层顺序错乱而合并或串行。\n"
    "2. 抽取文件里所有出现 SKU、产品编号、Part NO、Item No. 且旁边有价格的表格行。\n"
    "3. 一个 line_items 元素代表一个 SKU/产品。SKU 可来自「产品编号」「SKU」「内部SKU」「Part NO」「Item No.」，优先使用产品编号或明确写成 SKU 的值。\n"
    "4. 每个 SKU 还要尽量抽取材质及特殊特性、硬度、颜色；这些信息常在产品资料表或价格表中，不在采购订单主表时也要从同页其他表格读取。\n"
    "5. 同一个 SKU 如果有多个价格、MOQ 阶梯或数量阶梯，必须放入同一个元素的 prices 数组，不要只保留第一个价格；如果同一 SKU 同时有采购订单明细单价和产品价格表/价格阶梯，优先输出产品价格表/价格阶梯，不要再输出采购订单明细单价。\n"
    "6. 常见目标表包括采购订单明细表、报价表、产品资料表、价格阶梯表；常见表头包括 NO、Item No.、Part Name、Description、Unit、Qty、UnitPric、Amount、LeadTime、Remark，"
    "以及 编号、产品编号、代码、产品品名、Part NO.、材质及特殊特性、硬度、颜色、含税单价、最小定量、数量、价税合计、交货日期。\n"
    "7. 必须区分 MOQ 和订单数量：表头是 Qty/数量/采购数量/订购数量时，只填 quantity，不要填 moq；只有表头明确是 MOQ/最小定量/最小订量/起订量/最小起订量时才填 moq。\n"
    "8. 不要把含税总金额、价税合计总计、付款条款、验收条件、备注正文当成 SKU 价格。\n"
    "9. 数字按原表格内容读取：含税单价不要使用价税合计，价税合计不要使用含税总金额。\n"
    "10. 看不清或空白的单元格返回空字符串，不要推测。\n"
    "11. 只输出 JSON，不要输出说明文字或代码块。\n"
    "JSON 结构必须是：\n"
    "{\n"
    '  "line_items": [\n'
    "    {\n"
    '      "line_no": 1,\n'
    '      "sku": "535-10",\n'
    '      "product_code": "535-10",\n'
    '      "item_no": "ELB000232",\n'
    '      "product_name": "垫圈",\n'
    '      "part_no": "LX0107-001-0",\n'
    '      "material_special": "NBR",\n'
    '      "hardness": "50-55°A",\n'
    '      "color": "黑色",\n'
    '      "prices": [\n'
    '        {"price_type": "采购订单含税单价", "tax_included_price": "0.456", "quantity": "1,000", "unit": "Pcs", "amount": "456.00", "moq": "", "source_table": "采购订单明细"},\n'
    '        {"price_type": "价格阶梯", "tax_included_price": "0.42", "moq": "10K", "quantity": "", "unit": "", "amount": "", "source_table": "产品价格表"}\n'
    "      ],\n"
    '      "lead_time": "2026-7-10",\n'
    '      "remark": "",\n'
    '      "page": 1,\n'
    '      "source_table": "采购订单明细/产品价格表",\n'
    '      "confidence": 0.98,\n'
    '      "note": ""\n'
    "    }\n"
    "  ]\n"
    "}"
)


# ── 公开 API ──────────────────────────────────────────────────────────────────


async def extract_from_single_pdf(
    contents: bytes, fields_json: str
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """从单个 PDF 中提取字段，返回 (values_dict, notes_dict)。

    策略：
      1. 若存在文字层 → 同时发送文本 + 页面图片给 Claude（双模式）
         原因：契約金明細書等の表形式PDFはテキスト抽出が列順になり
         ラベルと値が混在するため、画像も合わせて正確に解析する。
      2. 若为扫描件   → 转为增强 JPEG 图片后发给 Claude 视觉模型
    """
    pdf_text = extract_text_from_pdf_bytes(contents)

    if pdf_text.strip():
        # 文字层 PDF：同时发送文本和图片（双模式），以便正确解析表格结构
        b64_images = pdf_bytes_to_base64_images(contents)

        content_parts = []
        # 先附上所有页面图片
        for b64 in b64_images:
            content_parts.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                }
            )
        # 再附上文本和提取指令
        content_parts.append(
            {
                "type": "text",
                "text": (
                    "以上は一つのPDF文書の全ページ画像です。\n"
                    "また、PDFから抽出したテキストも提供します（テキスト抽出は列順になるため"
                    "テーブルの値とラベルの対応が崩れている場合があります）。\n"
                    "画像を優先して正確にテーブル構造を理解し、テキストは補助として使用してください。\n"
                    "特に契約金明細書・精算書などの表形式文書では、画像から各行の項目と金額を"
                    "正確に読み取ること。消費税欄の0円をその項目の金額と混同しないこと。\n"
                    f"{_TABLE_READING_INSTRUCTION}\n"
                    f"{_NOTE_FIELD_INSTRUCTION}\n"
                    f"需要抽取的字段定义如下：\n{fields_json}\n"
                    f"以下はPDFから抽出したテキスト（参考）：\n{pdf_text}\n"
                    "现在请直接给出JSON："
                ),
            }
        )
        messages = [{"role": "user", "content": content_parts}]
    else:
        # 扫描件 PDF：先转图片
        b64_images = pdf_bytes_to_base64_images(contents)
        if not b64_images:
            raise RuntimeError("无法将PDF转换为图片，请检查PDF文件是否损坏。")

        content_parts = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            }
            for b64 in b64_images
        ]
        content_parts.append(
            {
                "type": "text",
                "text": (
                    "以上は一つのPDF文書の全ページ画像です（スキャン増強処理済み）。\n"
                    "各ページの文字・数字・表を正確に識別してください。\n"
                    "対于模糊、不清晰或需要推断的内容，请在note字段中用简短文字说明。\n"
                    f"{_TABLE_READING_INSTRUCTION}\n"
                    f"{_NOTE_FIELD_INSTRUCTION}\n"
                    f"需要抽取的字段定义如下：\n{fields_json}\n"
                    "现在请直接给出JSON："
                ),
            }
        )
        messages = [{"role": "user", "content": content_parts}]

    raw_text = await _call_claude(
        "あなたは日本の不動産契約書類（賃貸借契約書、重要事項説明書、賃貸決済明細書、"
        "契約金明細書、精算書等）を正確に読み取る専門家です。\n"
        "表形式の文書では各行の項目名と金額の対応を慎重に確認し、"
        "隣接行の値を取り違えないでください。\n"
        "指示されたJSON形式のみを出力してください。",
        messages,
        max_tokens=8000,
    )
    raw = _parse_json_from_model_output(raw_text)
    return _parse_raw_result(raw)


async def extract_line_items_from_pdf_vlm(
    contents: bytes,
    line_items_json: str,
) -> List[Dict[str, Any]]:
    """用 VLM 从 PDF 页面图片中识别采购/报价表格明细行。"""
    b64_images = pdf_bytes_to_base64_images(contents)
    if not b64_images:
        raise RuntimeError("无法将PDF转换为图片，请检查PDF文件是否损坏。")

    pdf_text = extract_text_from_pdf_bytes(contents)
    content_parts = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        }
        for b64 in b64_images
    ]
    content_parts.append(
        {
            "type": "text",
            "text": (
                f"{_LINE_ITEM_VLM_INSTRUCTION}\n\n"
                f"客户明细表配置如下：\n{line_items_json}\n\n"
                "如果配置中 source_hint 描述了目标表格，请优先按该描述选择表格。\n"
                f"PDF文字层仅供参考，不能替代图片表格判断：\n{pdf_text}\n"
                "现在请输出 JSON："
            ),
        }
    )

    raw_text = await _call_claude(
        "你是一个专门识别采购订单、报价单、商品明细表的多模态表格解析助手，只输出 JSON。",
        [{"role": "user", "content": content_parts}],
        max_tokens=12000,
    )
    raw = _parse_json_from_model_output(raw_text)
    return _parse_line_items_result(raw)


async def extract_line_items_from_image_vlm(
    contents: bytes,
    filename: str,
    line_items_json: str,
) -> List[Dict[str, Any]]:
    """用 VLM 从图片文件中识别采购/报价表格明细行。"""
    ext = Path(filename).suffix.lower()
    media_type = _MEDIA_TYPE_MAP.get(ext, "image/jpeg")
    b64 = base64.b64encode(contents).decode("utf-8")
    content_parts = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        },
        {
            "type": "text",
            "text": (
                f"{_LINE_ITEM_VLM_INSTRUCTION}\n\n"
                f"客户明细表配置如下：\n{line_items_json}\n"
                "现在请输出 JSON："
            ),
        },
    ]
    raw_text = await _call_claude(
        "你是一个专门识别采购订单、报价单、商品明细表的多模态表格解析助手，只输出 JSON。",
        [{"role": "user", "content": content_parts}],
        max_tokens=12000,
    )
    raw = _parse_json_from_model_output(raw_text)
    return _parse_line_items_result(raw)


async def analyze_pdf_for_fields(
    contents: bytes, filename: str
) -> list:
    """PDF の内容を AI で解析し、抽出可能なフィールド候補リストを返す。

    テンプレートなし・キーワード指定なしの場合に使用。
    AI が文書の種類を判定し、ユーザーにとって有用なフィールドを提案する。

    Returns:
        [{"id": "...", "label": "...", "description": "...", "required": bool}, ...]
    """
    ext = Path(filename).suffix.lower()

    if ext in ALLOWED_IMAGE_EXTS:
        # 画像ファイル
        media_type = _MEDIA_TYPE_MAP.get(ext, "image/jpeg")
        b64 = base64.b64encode(contents).decode("utf-8")
        content_parts = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
            {
                "type": "text",
                "text": (
                    "この画像を分析してください。\n"
                    "1. この文書は何の書類ですか？（種類を判定）\n"
                    "2. この文書から抽出できる重要な情報フィールドをリストアップしてください。\n\n"
                    "以下のJSON配列形式のみで出力してください（説明文・コードブロック不要）:\n"
                    '[\n'
                    '  {\n'
                    '    "id": "snake_case_id",\n'
                    '    "label": "日本語ラベル",\n'
                    '    "description": "このフィールドの説明（20文字以内）",\n'
                    '    "required": true\n'
                    '  }\n'
                    ']\n'
                    "フィールドは最大30個まで。JSONのみを返してください。"
                ),
            },
        ]
    else:
        # PDF ファイル
        pdf_text = extract_text_from_pdf_bytes(contents)
        b64_images = pdf_bytes_to_base64_images(contents)

        content_parts = []
        # 最大3ページ分の画像（解析用なので全ページは不要）
        for b64 in b64_images[:3]:
            content_parts.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                }
            )

        text_hint = ""
        if pdf_text.strip():
            text_hint = f"\n\nPDFから抽出したテキスト（参考）:\n{pdf_text[:3000]}"

        content_parts.append(
            {
                "type": "text",
                "text": (
                    "この文書を分析してください。\n"
                    "1. この文書は何の書類ですか？（種類を判定）\n"
                    "2. この文書から抽出できる重要な情報フィールドをリストアップしてください。\n\n"
                    "以下のJSON配列形式のみで出力してください（説明文・コードブロック不要）:\n"
                    '[\n'
                    '  {\n'
                    '    "id": "snake_case_id",\n'
                    '    "label": "日本語ラベル",\n'
                    '    "description": "このフィールドの説明（20文字以内）",\n'
                    '    "required": true\n'
                    '  }\n'
                    ']\n'
                    "フィールドは最大30個まで。JSONのみを返してください。"
                    f"{text_hint}"
                ),
            },
        )

    raw_text = await _call_claude(
        "あなたは文書解析の専門家です。文書の種類を判定し、抽出可能なフィールドを提案します。"
        "指示されたJSON配列のみを出力し、余分な説明は一切含めません。",
        [{"role": "user", "content": content_parts}],
    )

    # JSON パース
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", raw_text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if not match:
            raise RuntimeError(f"PDF解析結果のパースに失敗しました。AI 出力:\n{raw_text[:500]}")
        result = json.loads(match.group(0))

    if isinstance(result, dict):
        for v in result.values():
            if isinstance(v, list):
                result = v
                break

    if not isinstance(result, list):
        raise RuntimeError("PDF解析結果が配列形式ではありません。")

    sanitized = []
    for item in result:
        if not isinstance(item, dict):
            continue
        sanitized.append(
            {
                "id": str(item.get("id", f"field_{len(sanitized)}")),
                "label": str(item.get("label", item.get("id", ""))),
                "description": str(item.get("description", "")),
                "required": bool(item.get("required", True)),
            }
        )

    return sanitized


async def extract_by_user_prompt(
    contents: bytes, filename: str, user_prompt: str
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """ユーザーの自由記述プロンプトに基づいて PDF/画像から情報を抽出する。

    ユーザーが入力したテキストをそのまま AI への指示として渡し、
    AI が文書内容を解析して適切な情報を抽出する。

    Args:
        contents: ファイルバイト列
        filename: ファイル名
        user_prompt: ユーザーが入力した抽出指示テキスト

    Returns:
        (values_dict, notes_dict) — values_dict のキーは AI が判断したフィールド名
    """
    ext = Path(filename).suffix.lower()

    # 画像の場合
    if ext in ALLOWED_IMAGE_EXTS:
        media_type = _MEDIA_TYPE_MAP.get(ext, "image/jpeg")
        b64 = base64.b64encode(contents).decode("utf-8")
        content_parts = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
        ]
    else:
        # PDF の場合
        pdf_text = extract_text_from_pdf_bytes(contents)
        b64_images = pdf_bytes_to_base64_images(contents)

        content_parts = []
        for b64 in b64_images:
            content_parts.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                }
            )

        if pdf_text.strip():
            content_parts.append(
                {
                    "type": "text",
                    "text": f"PDFから抽出したテキスト（参考）:\n{pdf_text[:4000]}",
                }
            )

    # ユーザーの指示を AI に渡す
    content_parts.append(
        {
            "type": "text",
            "text": (
                "以上は文書の内容です。\n\n"
                f"ユーザーからの抽出指示:\n「{user_prompt}」\n\n"
                "上記の指示に基づいて、文書から該当する情報をできるだけ多く、網羅的に抽出してください。\n"
                "ユーザーの指示は日本語・中国語・英語など様々な言語で書かれますが、"
                "文書の内容は必ず原文のまま読み取り、抽出値は翻訳せず原文のまま返してください。\n\n"
                "結果は以下のJSON形式で出力してください（コードブロック不要）:\n"
                "{\n"
                '  "フィールド名": {"value": "抽出した値", "note": ""},\n'
                '  "フィールド名2": {"value": "抽出した値2", "note": ""}\n'
                "}\n\n"
                "ルール:\n"
                "- JSONのキー（フィールド名）にはユーザーが指定した1つ目の項目を入れること\n"
                "- JSONのvalue（値）にはユーザーが指定した2つ目の項目を入れること\n"
                "- 例: ユーザーが「企業名と品名」と指定した場合:\n"
                '  \"三菱重工業株式会社\": {\"value\": \"イージス・システム搭載艦, 12式地対艦誘導弾\", \"note\": \"\"}\n'
                "- ユーザーが1つの項目のみ指定した場合は、フィールド名を連番（項目名_1, 項目名_2…）にし、値にその内容を入れること\n"
                "- テーブルデータの場合は可能な限り全件抽出すること\n"
                "- noteフィールドは、不明瞭・推定の場合のみ記入し、通常は空文字列\n"
                "- JSON以外の文字は一切出力しないこと"
            ),
        }
    )

    raw_text = await _call_claude(
        "あなたは多言語対応の文書解析専門家です。"
        "ユーザーの指示がどの言語で書かれていても正確に理解し、"
        "文書から情報を網羅的に抽出します。"
        "指示されたJSON形式のみを出力し、余分な説明は一切含めません。",
        [{"role": "user", "content": content_parts}],
        max_tokens=8192,
    )
    raw = _parse_json_from_model_output(raw_text)
    return _parse_raw_result(raw)


async def extract_focused_page(
    contents: bytes,
    filename: str,
    fields_json: str,
    page_name: str,
    page_features: str,
    system_role: str,
    b64_images: Optional[List[str]] = None,
) -> Tuple[int, Dict[str, Any], Dict[str, str], Dict[str, list], int]:
    """アップロード書類から特定の種類のページを1つ判定し、そのページのみから抽出する。

    「契約書のみ」「精算書のみ」など、対象ページを絞るモードで共通利用する汎用関数。
      page_name     : 探すページの名称（例: 「賃貸借契約書（賃貸契約書）」「賃貸決済明細書・契約金明細書・精算書」）
      page_features : そのページにある典型的な情報の説明（判定の手がかり）
      system_role   : Claude へのシステムロール文

    あわせてページの向き（page_rotation）と、各値の位置 bbox（上向き補正後の
    0〜1000 正規化座標）を返す。表示側はページを page_rotation だけ回転して
    上向きにするため、bbox と表示が一致する。

    Returns:
        (page_index_0based, values_dict, notes_dict, boxes_dict, page_rotation)
    """
    ext = Path(filename).suffix.lower()

    if ext in ALLOWED_IMAGE_EXTS:
        media_type = _MEDIA_TYPE_MAP.get(ext, "image/jpeg")
        b64 = base64.b64encode(contents).decode("utf-8")
        content_parts = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            }
        ]
        num_pages = 1
        page_hint = "この画像は1ページのみです。target_page は必ず 1 にしてください。"
    else:
        # b64_images が渡された場合は再生成せず共有する（総合2パスでの画像2重生成を回避）
        if b64_images is None:
            b64_images = pdf_bytes_to_base64_images(contents)
        if not b64_images:
            raise RuntimeError("无法将PDF转换为图片，请检查PDF文件是否损坏。")
        content_parts = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            }
            for b64 in b64_images
        ]
        num_pages = len(b64_images)
        page_hint = (
            f"この文書は全{num_pages}ページです。各画像は先頭から順に "
            f"1, 2, 3 … ページ目です。"
        )

    content_parts.append(
        {
            "type": "text",
            "text": (
                "以上はアップロードされた書類の全ページ画像です。\n"
                f"この中から『{page_name}』に該当するページを1つだけ特定してください。\n"
                f"{page_features}\n"
                f"{page_hint}\n"
                "特定したページ番号（1始まり）を target_page に入れ、"
                "そのページの内容から以下のフィールドを抽出してください。"
                "他のページの情報は使用しないこと。\n"
                "また、その対象ページの向きを判定してください。"
                "top_edge = そのページの『上端』（タイトルや本文の先頭行がある側）が、"
                "現在この画像内のどの辺にあるかを、次の語から1つだけ選ぶこと：\n"
                "  \"top\"    = 既に正しい向き（文字が普通に読める、上端が画像の上辺）\n"
                "  \"bottom\" = 上下さかさま（上端が画像の下辺）\n"
                "  \"left\"   = ページが左へ倒れている（上端が画像の左辺）\n"
                "  \"right\"  = ページが右へ倒れている（上端が画像の右辺）\n"
                "判定のコツ：文字が普通に左→右に読める向きが正しい向き(top)。"
                "縦に倒れて見える場合、文字の頭（行の先頭）が画像のどちら側にあるかで left/right を決める。\n"
                "さらに、各フィールドについて、その値が記載されている位置を矩形座標 bbox として返してください。\n"
                "  - 重要：bbox は『ページを正しい縦向き（上端が上）に直した後』の状態を基準に、"
                "左上を (0,0)、右下を (1000,1000) とする 0〜1000 の整数で表すこと。\n"
                "  - 形式：[左x, 上y, 右x, 下y]（値の文字を囲む矩形）。\n"
                "  - 値が空、または位置が特定できない場合は bbox を空配列 [] にすること。\n"
                f"{_NOTE_FIELD_INSTRUCTION}\n"
                f"{_STRUCTURED_FIELD_RULE_INSTRUCTION}\n"
                f"{_SETTLEMENT_AMOUNT_INSTRUCTION}\n"
                "出力は次のJSON形式のみ（コードブロック・説明文は一切不要）:\n"
                "{\n"
                '  "target_page": <対象ページの番号（1始まりの整数）>,\n'
                '  "top_edge": "top|bottom|left|right",\n'
                '  "fields": { "field_id": {"value": "...", "note": "", "bbox": [左x,上y,右x,下y]}, ... }\n'
                "}\n"
                f"抽出するフィールド定義:\n{fields_json}\n"
                "现在请直接给出JSON："
            ),
        }
    )

    raw_text = await _call_claude(
        system_role,
        [{"role": "user", "content": content_parts}],
        max_tokens=6000,
    )

    raw = _parse_json_from_model_output(raw_text)

    page_1based = raw.get("target_page", raw.get("contract_page", 1))
    try:
        page_idx = int(page_1based) - 1
    except (ValueError, TypeError):
        page_idx = 0
    # 実ページ範囲にクランプ（AI の誤った番号で表示ページが空になるのを防ぐ）
    page_idx = max(0, min(page_idx, num_pages - 1))

    # ページの向き：AI には「上端が今どの辺にあるか(top_edge)」を聞き、
    # ここで確定的に「上向きにするための時計回り回転角」へ換算する。
    # （"時計回り何度" を直接 AI に答えさせると方向を取り違えやすいため）
    _EDGE_TO_CW = {"top": 0, "right": 270, "left": 90, "bottom": 180}
    top_edge = str(raw.get("top_edge", "top")).strip().lower()
    page_rotation = _EDGE_TO_CW.get(top_edge, 0)

    fields_raw = raw.get("fields", {})
    if not isinstance(fields_raw, dict):
        fields_raw = {}

    values: Dict[str, Any] = {}
    notes: Dict[str, str] = {}
    boxes: Dict[str, list] = {}
    for k, v in fields_raw.items():
        value, note = _parse_field_entry(v)
        values[k] = value
        notes[k] = note
        if value and isinstance(v, dict):
            bbox = v.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                try:
                    coords = [float(c) for c in bbox]
                    # [左, 上, 右, 下] の順に正規化（座標が逆でも吸収）
                    x0, y0 = min(coords[0], coords[2]), min(coords[1], coords[3])
                    x1, y1 = max(coords[0], coords[2]), max(coords[1], coords[3])
                    if x1 > x0 and y1 > y0:
                        boxes[k] = [x0, y0, x1, y1]
                except (ValueError, TypeError):
                    pass
    return page_idx, values, notes, boxes, page_rotation


async def extract_from_image(
    contents: bytes, filename: str, fields_json: str
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """从单张图片（物件截图等）中提取字段，返回 (values_dict, notes_dict)。"""
    ext = Path(filename).suffix.lower()
    media_type = _MEDIA_TYPE_MAP.get(ext, "image/jpeg")
    b64 = base64.b64encode(contents).decode("utf-8")

    content_parts = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        },
        {
            "type": "text",
            "text": (
                "这是一张与不动产物件相关的图片（可能是网络截图、物件详情页等）。\n"
                "请仔细识别图片中的所有文字和信息，从中抽取指定字段。\n"
                f"{_NOTE_FIELD_INSTRUCTION}\n"
                f"需要抽取的字段定义如下：\n{fields_json}\n"
                "现在请直接给出JSON："
            ),
        },
    ]

    raw_text = await _call_claude(
        "你是一个严谨的结构化信息抽取助手，只输出JSON。",
        [{"role": "user", "content": content_parts}],
        max_tokens=8000,
    )
    raw = _parse_json_from_model_output(raw_text)
    return _parse_raw_result(raw)
