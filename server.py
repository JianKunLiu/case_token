# -*- coding: utf-8 -*-
"""
飞书文档内容获取 HTTP API
可被 FastGPT 通过 HTTP 直接调用

接口:
  POST /get_document  入参 {"app_id": "...", "app_secret": "...", "document_id": "..."}
                      返回 {"text": "..."}
  GET  /health        健康检查
  GET  /static/xxx    图片文件
"""
import os
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from feishu_client import fetch_document

# ---------- 日志 ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------- Flask ----------
app = Flask(__name__)
CORS(app)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")


# ==================== 接口 ====================

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/get_document", methods=["POST"])
def get_document():
    """
    获取飞书文档的文字（含图片 Markdown 引用）
    入参 JSON: {"app_id": "...", "app_secret": "...", "document_id": "..."}
    返回 JSON: {"text": "..."}
    """
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "请求体不是合法 JSON"}), 400

    app_id = body.get("app_id", "")
    app_secret = body.get("app_secret", "")
    document_id = body.get("document_id", "")

    if not app_id:
        return jsonify({"error": "缺少 app_id 参数"}), 400
    if not app_secret:
        return jsonify({"error": "缺少 app_secret 参数"}), 400
    if not document_id:
        return jsonify({"error": "缺少 document_id 参数"}), 400

    logger.info(f"获取文档: {document_id}")

    try:
        result = fetch_document(app_id, app_secret, document_id, base_url=BASE_URL)
        return jsonify(result)
    except Exception as e:
        logger.exception(f"获取文档失败: {document_id}")
        return jsonify({"error": str(e)}), 500


@app.route("/static/<path:filename>")
def serve_static(filename):
    """提供下载的图片文件"""
    return send_from_directory(STATIC_DIR, filename)


# ==================== 启动 ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    host = os.environ.get("HOST", "0.0.0.0")
    debug = os.environ.get("DEBUG", "true").lower() == "true"

    logger.info(f"Server running on {host}:{port}")
    logger.info(f"Base URL: {BASE_URL}")

    app.run(host=host, port=port, debug=debug)
