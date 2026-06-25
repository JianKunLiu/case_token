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
BLOCK_TASK = 31
BLOCK_UNDEFINED = 999  # 未支持/已失效的 block 类型

HEADING_LEVELS = {
    BLOCK_HEADING_1: 1, BLOCK_HEADING_2: 2, BLOCK_HEADING_3: 3,
    BLOCK_HEADING_4: 4, BLOCK_HEADING_5: 5, BLOCK_HEADING_6: 6,
    BLOCK_HEADING_7: 7, BLOCK_HEADING_8: 8, BLOCK_HEADING_9: 9,
}


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
                        image_url_map: dict = None, indent_level: int = 0) -> str:
    """递归将 block 列表转为 Markdown"""
    lines = []
    blocks_by_parent = {}
    ordered_blocks = []

    for b in blocks:
        pid = b.get("parent_id", "")
        if pid == parent_id:
            ordered_blocks.append(b)
        blocks_by_parent.setdefault(pid, []).append(b)

    for b in ordered_blocks:
        btype = b.get("block_type", 0)
        bid = b.get("block_id", "")
        children = blocks_by_parent.get(bid, [])

        # ------- 未支持/已失效的 block，跳过自身及所有子 block -------
        if btype == BLOCK_UNDEFINED:
            logger.warning(f"跳过失效/未支持的 block: {bid}")
            continue

        # ------- 标题 -------
        if btype in HEADING_LEVELS:
            level = HEADING_LEVELS[btype]
            text = _extract_text(b.get("text", {}).get("elements", []), image_url_map) or b.get("heading{}".format(level), {}).get("elements", [])
            if not text:
                text = _extract_text(b.get("heading{}".format(level), {}).get("elements", []), image_url_map)
            lines.append(f"{'#' * level} {text}".strip())
            lines.append("")

        # ------- 普通段落 -------
        elif btype == BLOCK_TEXT:
            text = _extract_text(b.get("text", {}).get("elements", []), image_url_map)
            if text.strip():
                lines.append(text)
                lines.append("")

        # ------- 无序列表 -------
        elif btype == BLOCK_BULLET:
            text = _extract_text(b.get("text", {}).get("elements", []), image_url_map)
            prefix = "  " * indent_level + "- "
            lines.append(f"{prefix}{text}")

        # ------- 有序列表 -------
        elif btype == BLOCK_ORDERED:
            text = _extract_text(b.get("text", {}).get("elements", []), image_url_map)
            prefix = "  " * indent_level + "1. "
            lines.append(f"{prefix}{text}")

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
            text = _extract_text(b.get("text", {}).get("elements", []), image_url_map)
            for line in text.split("\n"):
                lines.append(f"> {line}")
            lines.append("")

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

        # ------- 表格 -------
        elif btype == BLOCK_TABLE:
            rows = _extract_table_rows(b, blocks_by_parent)
            if rows:
                lines.append(_render_markdown_table(rows))
                lines.append("")

        # ------- 容器类递归处理子块 -------
        elif btype in (BLOCK_PAGE, BLOCK_GRID, BLOCK_GRID_COLUMN, BLOCK_CALLOUT):
            if children:
                child_md = _blocks_to_markdown(children, parent_id=bid,
                                               image_url_map=image_url_map,
                                               indent_level=indent_level)
                if child_md.strip():
                    lines.append(child_md)

        # ------- 嵌套子块（列表项的子项等） -------
        if children and btype not in (BLOCK_PAGE, BLOCK_GRID, BLOCK_GRID_COLUMN,
                                       BLOCK_TABLE, BLOCK_CALLOUT):
            child_md = _blocks_to_markdown(children, parent_id=bid,
                                           image_url_map=image_url_map,
                                           indent_level=indent_level + 1)
            if child_md.strip():
                lines.append(child_md)

    return "\n".join(lines)


def _extract_table_rows(table_block: dict, blocks_by_parent: dict) -> list:
    """从表格 block 提取行数据"""
    table_id = table_block.get("block_id", "")
    rows = []
    row_blocks = [b for b in blocks_by_parent.get(table_id, [])
                  if b.get("block_type") == BLOCK_TABLE_CELL]

    # 按 table 的 row/col 属性组织单元格
    # 简化处理：取所有直接子块中的文本
    cells_text = []
    for cell in row_blocks:
        text = _extract_text(cell.get("text", {}).get("elements", []))
        cells_text.append(text)

    # 尝试按表格行列重组
    row_size = table_block.get("table", {}).get("property", {}).get("column_size", len(cells_text))
    if row_size > 0:
        for i in range(0, len(cells_text), row_size):
            rows.append(cells_text[i:i + row_size])
    elif cells_text:
        rows = [cells_text]

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
    一站式：获取文档文字 + 图片，返回结构化 JSON
    入参: app_id, app_secret, document_id (均由外部传入)
    返回: {"text": "纯文本", "images": ["http://...", ...]}
    """
    client = FeishuClient(app_id, app_secret)

    # 1. 获取纯文本
    text = client.get_raw_content(document_id)

    # 1.1 清理 raw_content 中可能存在的失效引用占位文本，并清理多余空行
    import re
    invalid_patterns = [
        r'\[引用内容[^\]]*失效[^\]]*\]',
        r'\[引用内容[^\]]*不可用[^\]]*\]',
        r'\[引用[^\]]*已失效[^\]]*\]',
        r'\[内容[^\]]*不可用[^\]]*\]',
    ]
    for pattern in invalid_patterns:
        text = re.sub(pattern, '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 2. 获取 blocks 并提取图片
    blocks = client.get_blocks(document_id)
    image_tokens = [b.get("image", {}).get("token") for b in blocks
                    if b.get("block_type") == 27 and b.get("image", {}).get("token")]

    # 3. 下载图片到 static 目录
    image_urls = []
    for token in image_tokens:
        try:
            local_path = client.download_image(token)
            filename = os.path.basename(local_path)
            image_url = f"{base_url.rstrip('/')}/static/{filename}"
            image_urls.append(image_url)
        except Exception as e:
            logger.error(f"下载图片失败 token={token}: {e}")
            image_urls.append({
                "file_token": token,
                "error": str(e),
            })

    return {
        "text": text,
        "images": image_urls,
    }
