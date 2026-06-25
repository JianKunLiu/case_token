# -*- coding: utf-8 -*-
"""
飞书 API 客户端
- 获取 tenant_access_token
- 获取文档文字内容 (raw_content)
- 获取文档块内容（含图片 blocks）
- 下载图片到本地
"""
import time
import os
import requests
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://open.feishu.cn/open-apis"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# ---------- Token 缓存（按 app_id 区分） ----------
_token_cache = {}  # {(app_id, app_secret): {"token": ..., "expire_at": ...}}

# ---------- 全局引用：供 _blocks_to_markdown 调用 sheet 读取 ----------
_feishu_client_ref = None


def _get_token(app_id: str, app_secret: str) -> str:
    """获取 tenant_access_token，带缓存自动刷新"""
    cache_key = (app_id, app_secret)
    entry = _token_cache.get(cache_key)
    now = time.time()

    if entry and entry["token"] and now < entry["expire_at"] - 60:
        return entry["token"]

    url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": app_id,
        "app_secret": app_secret,
    }, timeout=15)
    data = resp.json()
    logger.info(f"Login response code: {data.get('code')}")

    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data}")

    _token_cache[cache_key] = {
        "token": data["tenant_access_token"],
        "expire_at": now + data.get("expire", 7200),
    }
    return _token_cache[cache_key]["token"]


class FeishuClient:
    """飞书文档客户端，每次请求传入 app_id / app_secret"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    def _headers(self) -> dict:
        token = _get_token(self.app_id, self.app_secret)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def get_raw_content(self, document_id: str) -> str:
        """获取文档文字内容"""
        url = f"{BASE_URL}/docx/v1/documents/{document_id}/raw_content"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        data = resp.json()
        logger.info(f"raw_content code: {data.get('code')}")

        if data.get("code") != 0:
            raise Exception(f"获取文档内容失败: {data}")

        return data.get("data", {}).get("content", "")

    def get_blocks(self, document_id: str) -> list:
        """获取文档 Block 列表（含图片信息）"""
        url = f"{BASE_URL}/docx/v1/documents/{document_id}/blocks"
        all_blocks = []
        page_token = None

        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token

            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
            data = resp.json()
            logger.info(f"blocks code: {data.get('code')}")

            if data.get("code") != 0:
                raise Exception(f"获取 blocks 失败: {data}")

            all_blocks.extend(data.get("data", {}).get("items", []))

            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token")

        return all_blocks

    def get_sheet_data(self, sheet_token: str) -> list:
        """获取内嵌电子表格的数据，返回二维数组（首行为表头）

        sheet_token 格式: {SpreadsheetToken}_{SheetID}
        """
        parts = sheet_token.split("_", 1)
        if len(parts) < 2:
            logger.warning(f"Sheet token 格式异常: {sheet_token}")
            return []
        spreadsheet_token, sheet_id = parts[0], parts[1]

        url = f"{BASE_URL}/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(f"获取电子表格数据失败: {data}")
            return []

        values = data.get("data", {}).get("valueRange", {}).get("values", [])
        # 过滤掉全为 None 的行，并将富文本单元格转为纯文本
        result = []
        for row in values:
            if not any(v is not None and str(v).strip() for v in row):
                continue
            clean_row = []
            for cell in row:
                if cell is None:
                    clean_row.append("")
                elif isinstance(cell, list):
                    # 富文本数组，提取 text 字段拼接
                    text_parts = []
                    for seg in cell:
                        if isinstance(seg, dict):
                            text_parts.append(seg.get("text", ""))
                        else:
                            text_parts.append(str(seg))
                    clean_row.append("".join(text_parts))
                else:
                    clean_row.append(str(cell))
            result.append(clean_row)
        return result

    def download_image(self, file_token: str, save_dir: str = None) -> str:
        """下载飞书图片到本地，返回本地文件路径"""
        if save_dir is None:
            save_dir = STATIC_DIR

        os.makedirs(save_dir, exist_ok=True)

        url = f"{BASE_URL}/drive/v1/medias/{file_token}/download"
        resp = requests.get(url, headers=self._headers(), timeout=60)

        if resp.status_code != 200:
            raise Exception(f"下载图片失败: status={resp.status_code}")

        content_type = resp.headers.get("Content-Type", "image/png")
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
            "image/svg+xml": ".svg",
        }
        ext = ext_map.get(content_type, ".png")

        filename = f"{file_token}{ext}"
        filepath = os.path.join(save_dir, filename)

        with open(filepath, "wb") as f:
            f.write(resp.content)

        logger.info(f"Downloaded image: {filepath}")
        return filepath


# ---------- Block → Markdown 转换 ----------

# 飞书 docx 块类型
BLOCK_PAGE = 1
BLOCK_TEXT = 2
BLOCK_HEADING_1 = 3
BLOCK_HEADING_2 = 4
BLOCK_HEADING_3 = 5
BLOCK_HEADING_4 = 6
BLOCK_HEADING_5 = 7
BLOCK_HEADING_6 = 8
BLOCK_HEADING_7 = 9
BLOCK_HEADING_8 = 10
BLOCK_HEADING_9 = 11
BLOCK_BULLET = 12
BLOCK_ORDERED = 13
BLOCK_CODE = 14
BLOCK_QUOTE = 15
BLOCK_CALLOUT = 16
BLOCK_DIVIDER = 17
BLOCK_TABLE = 18
BLOCK_TABLE_CELL = 19
BLOCK_GRID = 21
BLOCK_GRID_COLUMN = 22
BLOCK_IMAGE = 27
BLOCK_SHEET = 30          # 新版电子表格容器
BLOCK_TABLE_CONTAINER = 31  # 新版表格容器
BLOCK_TABLE_CELL_V2 = 32    # 新版表格单元格
BLOCK_TASK = 31  # 注意：与 TABLE_CONTAINER 共用 31，需按上下文区分
BLOCK_UNDEFINED = 999  # 未支持/已失效的 block 类型

HEADING_LEVELS = {
    BLOCK_HEADING_1: 1, BLOCK_HEADING_2: 2, BLOCK_HEADING_3: 3,
    BLOCK_HEADING_4: 4, BLOCK_HEADING_5: 5, BLOCK_HEADING_6: 6,
    BLOCK_HEADING_7: 7, BLOCK_HEADING_8: 8, BLOCK_HEADING_9: 9,
}


# 字段名映射：block_type → 存储文本元素的字段名（新版飞书 API 使用命名字段而非 text 字段）
_TEXT_FIELD_MAP = {
    3: "heading1", 4: "heading2", 5: "heading3",
    7: "heading4", 8: "heading6", 9: "heading7",
    10: "heading8", 11: "heading9",
    12: "bullet",    # 无序列表
    13: "ordered",   # 有序列表
    14: "code",      # 代码块
    15: "quote",     # 引用块
}
# 标题类型也需要尝试 heading 字段（优先级高于 text）
_HEADING_TYPES = {3, 4, 5, 7, 8, 9, 10, 11}


def _get_block_elements(block: dict) -> list:
    """从 block 中获取文本元素列表，优先使用命名字段（新版 API），fallback 到 text 字段"""
    btype = block.get("block_type", 0)

    # 标题类型：先尝试 headingN 字段
    if btype in _HEADING_TYPES:
        field_name = _TEXT_FIELD_MAP.get(btype, "text")
        elements = block.get(field_name, {}).get("elements", [])
        if elements:
            return elements

    # 其他类型：按映射表查找
    field_name = _TEXT_FIELD_MAP.get(btype)
    if field_name:
        elements = block.get(field_name, {}).get("elements", [])
        if elements:
            return elements

    # fallback: text 字段
    return block.get("text", {}).get("elements", [])


def _extract_text(elements: list, image_url_map: dict = None) -> str:
    """从 text.elements 列表中提取纯文本，支持内联样式"""
    result = []
    for el in elements:
        if el.get("text_run"):
            content = el["text_run"].get("content", "")
            style = el["text_run"].get("text_element_style", {})
            # 加粗
            if style.get("bold"):
                content = f"**{content}**"
            # 斜体
            if style.get("italic"):
                content = f"*{content}*"
            # 删除线：内容已被划掉，视为无效，跳过不输出
            if style.get("strikethrough"):
                continue
            # 行内代码
            if style.get("inline_code"):
                content = f"`{content}`"
            # 链接
            link = el["text_run"].get("text_element_style", {}).get("link")
            if link:
                content = f"[{content}]({link.get('url', '')})"
            result.append(content)
        elif el.get("mention_user"):
            result.append(f"@{el['mention_user'].get('name', '')}")
        elif el.get("mention_doc"):
            result.append(f"[{el['mention_doc'].get('title', '')}]({el['mention_doc'].get('url', '')})")
        elif el.get("inline_image") and image_url_map:
            token = el["inline_image"].get("file_token", "")
            url = image_url_map.get(token, "")
            if url:
                result.append(f"![]({url})")
    return "".join(result)


def _blocks_to_markdown(blocks: list, parent_id: str = None,
                        image_url_map: dict = None, indent_level: int = 0,
                        blocks_by_parent: dict = None) -> str:
    """递归将 block 列表转为 Markdown

    blocks: 当前层级需要处理的 blocks 列表
    blocks_by_parent: 全局的 parent_id → children 映射（用于任意深度的子 block 查找）
    """
    if blocks_by_parent is None:
        blocks_by_parent = {}
        for b in blocks:
            pid = b.get("parent_id", "")
            blocks_by_parent.setdefault(pid, []).append(b)

    lines = []
    ordered_blocks = []

    for b in blocks:
        pid = b.get("parent_id", "")
        if pid == parent_id:
            ordered_blocks.append(b)

    for b in ordered_blocks:
        btype = b.get("block_type", 0)
        bid = b.get("block_id", "")
        children = blocks_by_parent.get(bid, [])

        # ------- 未支持/已失效的 block，跳过自身及所有子 block -------
        if btype == BLOCK_UNDEFINED:
            logger.warning(f"跳过失效/未支持的 block: {bid}")
            continue

        # 标记：当前 block 的文本内容是否全为删除线（应跳过自身及子 block）
        skip_self_and_children = False

        # ------- 标题 -------
        if btype in HEADING_LEVELS:
            level = HEADING_LEVELS[btype]
            text = _extract_text(_get_block_elements(b), image_url_map)
            # 标题内容全被删除线划掉 → 跳过此标题及其所有子 block
            if not text.strip():
                skip_self_and_children = True
            else:
                lines.append(f"{'#' * level} {text}".strip())
                lines.append("")

        # ------- 普通段落 -------
        elif btype == BLOCK_TEXT:
            text = _extract_text(_get_block_elements(b), image_url_map)
            if text.strip():
                lines.append(text)
                lines.append("")
            else:
                skip_self_and_children = True

        # ------- 无序列表 -------
        elif btype == BLOCK_BULLET:
            text = _extract_text(_get_block_elements(b), image_url_map)
            if text.strip():
                prefix = "  " * indent_level + "- "
                lines.append(f"{prefix}{text}")
            else:
                skip_self_and_children = True

        # ------- 有序列表 -------
        elif btype == BLOCK_ORDERED:
            text = _extract_text(_get_block_elements(b), image_url_map)
            if text.strip():
                prefix = "  " * indent_level + "1. "
                lines.append(f"{prefix}{text}")
            else:
                skip_self_and_children = True

        # ------- 代码块 -------
        elif btype == BLOCK_CODE:
            lang = b.get("code", {}).get("language", "")
            text = _extract_text(b.get("code", {}).get("elements", []), image_url_map)
            lines.append(f"```{lang}")
            lines.append(text)
            lines.append("```")
            lines.append("")

        # ------- 引用 -------
        elif btype == BLOCK_QUOTE:
            text = _extract_text(_get_block_elements(b), image_url_map)
            if text.strip():
                for line in text.split("\n"):
                    lines.append(f"> {line}")
                lines.append("")
            else:
                skip_self_and_children = True

        # ------- 分割线 -------
        elif btype == BLOCK_DIVIDER:
            lines.append("---")
            lines.append("")

        # ------- 图片 -------
        elif btype == BLOCK_IMAGE and image_url_map:
            token = b.get("image", {}).get("token", "")
            url = image_url_map.get(token, "")
            if url:
                lines.append(f"![]({url})")
                lines.append("")

        # ------- 旧版表格 (block_type=18) -------
        elif btype == BLOCK_TABLE:
            rows = _extract_table_rows(b, blocks_by_parent)
            if rows:
                lines.append(_render_markdown_table(rows))
                lines.append("")
            else:
                # 降级：将表格所有子块的文本直接输出
                table_id = b.get("block_id", "")
                table_children = blocks_by_parent.get(table_id, [])
                child_texts = []
                for cb in table_children:
                    t = _extract_text(_get_block_elements(cb), image_url_map)
                    if t.strip():
                        child_texts.append(t)
                if child_texts:
                    lines.append("\n".join(child_texts))
                    lines.append("")

        # ------- 新版表格 (block_type=31, table 容器) -------
        elif btype == BLOCK_TABLE_CONTAINER:
            rows = _extract_table_rows_v2(b, blocks, image_url_map)
            if rows:
                lines.append(_render_markdown_table(rows))
                lines.append("")

        # ------- 新版表格单元格 (block_type=32) -------
        # 单元格由 TABLE_CONTAINER 处理，这里跳过（不产生输出）
        elif btype == BLOCK_TABLE_CELL_V2:
            pass

        # ------- 新版电子表格 (block_type=30, sheet) -------
        # sheet 是内嵌电子表格，通过 token 读取数据后渲染为 Markdown 表格
        elif btype == BLOCK_SHEET:
            sheet_token = b.get("sheet", {}).get("token", "")
            if sheet_token and _feishu_client_ref:
                sheet_rows = _feishu_client_ref.get_sheet_data(sheet_token)
                if sheet_rows:
                    lines.append(_render_markdown_table(sheet_rows))
                    lines.append("")
                else:
                    # 降级：如果获取失败，递归处理子块
                    if children:
                        child_md = _blocks_to_markdown(children, parent_id=bid,
                                                       image_url_map=image_url_map,
                                                       indent_level=indent_level,
                                                       blocks_by_parent=blocks_by_parent)
                        if child_md.strip():
                            lines.append(child_md)
            elif children:
                child_md = _blocks_to_markdown(children, parent_id=bid,
                                               image_url_map=image_url_map,
                                               indent_level=indent_level,
                                               blocks_by_parent=blocks_by_parent)
                if child_md.strip():
                    lines.append(child_md)

        # ------- 容器类递归处理子块 -------
        elif btype in (BLOCK_PAGE, BLOCK_GRID, BLOCK_GRID_COLUMN, BLOCK_CALLOUT):
            if children:
                child_md = _blocks_to_markdown(children, parent_id=bid,
                                               image_url_map=image_url_map,
                                               indent_level=indent_level,
                                               blocks_by_parent=blocks_by_parent)
                if child_md.strip():
                    lines.append(child_md)

        # ------- 嵌套子块（列表项的子项等） -------
        if children and btype not in (BLOCK_PAGE, BLOCK_GRID, BLOCK_GRID_COLUMN,
                                       BLOCK_TABLE, BLOCK_TABLE_CONTAINER, BLOCK_TABLE_CELL_V2,
                                       BLOCK_SHEET, BLOCK_CALLOUT) and not skip_self_and_children:
            child_md = _blocks_to_markdown(children, parent_id=bid,
                                           image_url_map=image_url_map,
                                           indent_level=indent_level + 1,
                                           blocks_by_parent=blocks_by_parent)
            if child_md.strip():
                lines.append(child_md)

    return "\n".join(lines)


def _extract_table_rows(table_block: dict, blocks_by_parent: dict) -> list:
    """从表格 block 提取行数据"""
    table_id = table_block.get("block_id", "")
    rows = []
    all_children = blocks_by_parent.get(table_id, [])
    row_blocks = [b for b in all_children
                  if b.get("block_type") == BLOCK_TABLE_CELL]

    logger.info(f"Table {table_id}: total_children={len(all_children)}, cell_blocks={len(row_blocks)}")
    for b in all_children:
        logger.info(f"  child type={b.get('block_type')} id={b.get('block_id')[:16]}... keys={list(b.keys())[:6]}")

    # 按 table 的 row/col 属性组织单元格
    # 简化处理：取所有直接子块中的文本
    cells_text = []
    for cell in row_blocks:
        text = _extract_text(_get_block_elements(cell))
        cells_text.append(text)

    # 尝试按表格行列重组
    table_prop = table_block.get("table", {})
    row_size = table_prop.get("property", {}).get("column_size", 0)
    if not row_size:
        # 某些版本可能在 table.row_size
        row_size = table_prop.get("row_size", 0)
    if not row_size:
        row_size = len(cells_text)
    if row_size > 0 and cells_text:
        for i in range(0, len(cells_text), row_size):
            rows.append(cells_text[i:i + row_size])

    return rows


def _extract_table_rows_v2(table_block: dict, all_blocks: list, image_url_map: dict = None) -> list:
    """从新版表格 block (type=31) 提取行数据，按 cells 顺序 + column_size 重组"""
    table_info = table_block.get("table", {})
    column_size = table_info.get("property", {}).get("column_size", 1)
    cell_ids = table_info.get("cells", [])
    if not cell_ids:
        return []

    # 建立 block_id → block 的索引
    block_map = {b.get("block_id"): b for b in all_blocks}

    # 按 cells 顺序提取每个单元格的文本
    cell_texts = []
    for cell_id in cell_ids:
        cell_block = block_map.get(cell_id, {})
        # 单元格 (type=32) 的内容在 children 引用的子 block 里
        text_parts = []
        for child_id in cell_block.get("children", []):
            child = block_map.get(child_id, {})
            t = _extract_text(_get_block_elements(child), image_url_map)
            if t.strip():
                text_parts.append(t)
        cell_texts.append(" ".join(text_parts))

    # 按列数拆分成行
    rows = []
    for i in range(0, len(cell_texts), column_size):
        rows.append(cell_texts[i:i + column_size])

    return rows


def _render_markdown_table(rows: list) -> str:
    """将二维列表渲染成 Markdown 表格"""
    if not rows:
        return ""
    max_cols = max(len(r) for r in rows)

    lines = []
    # 表头
    header = rows[0] + [""] * (max_cols - len(rows[0]))
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")

    # 数据行
    for row in rows[1:]:
        padded = row + [""] * (max_cols - len(row))
        lines.append("| " + " | ".join(padded) + " |")

    return "\n".join(lines)


def blocks_to_markdown(blocks: list, image_url_map: dict = None) -> str:
    """将飞书文档 blocks 转为 Markdown 文本"""
    if image_url_map is None:
        image_url_map = {}
    return _blocks_to_markdown(blocks, image_url_map=image_url_map)


def fetch_document(app_id: str, app_secret: str, document_id: str,
                   base_url: str = "http://localhost:5000") -> dict:
    """
    一站式：获取文档文字（含图片 Markdown 引用），返回结构化 JSON
    入参: app_id, app_secret, document_id (均由外部传入)
    返回: {"text": "..."}
    """
    global _feishu_client_ref
    client = FeishuClient(app_id, app_secret)
    _feishu_client_ref = client  # 供 _blocks_to_markdown 中读取 sheet 数据

    # 1. 获取 blocks
    blocks = client.get_blocks(document_id)

    # 2. 下载图片到 static 目录，并构建 token→URL 映射（用于 Markdown 内 ![]() 引用）
    image_tokens = [b.get("image", {}).get("token") for b in blocks
                    if b.get("block_type") == 27 and b.get("image", {}).get("token")]
    image_url_map = {}
    for token in image_tokens:
        try:
            local_path = client.download_image(token)
            filename = os.path.basename(local_path)
            image_url = f"{base_url.rstrip('/')}/static/{filename}"
            image_url_map[token] = image_url
        except Exception as e:
            logger.error(f"下载图片失败 token={token}: {e}")

    # 3. 用 blocks 生成 Markdown（会跳过删除线内容和失效 block）
    # document_id 即为根 PAGE block 的 ID，子块的 parent_id 指向它
    text = _blocks_to_markdown(blocks, parent_id=document_id, image_url_map=image_url_map)

    # 3.1 清理多余空行
    import re
    text = re.sub(r'\n{3,}', '\n\n', text)

    return {
        "text": text,
    }
