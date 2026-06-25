#!/bin/bash
# 飞书文档 API 一键部署脚本
# 在服务器上执行: bash deploy.sh

set -e

echo "=== 开始部署 ==="

# 安装基础依赖
if ! command -v python3 &> /dev/null; then
    echo "安装 Python3..."
    apt-get update && apt-get install -y python3 python3-pip
fi

if ! command -v docker &> /dev/null; then
    echo "安装 Docker..."
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
fi

# 创建目录
mkdir -p /opt/feishu-doc-api
cd /opt/feishu-doc-api

# 获取公网IP
PUBLIC_IP=$(curl -s http://icanhazip.com || curl -s http://ifconfig.me)
echo "检测到公网IP: $PUBLIC_IP"

# 创建 requirements.txt
cat > requirements.txt << 'EOF'
flask>=3.0
flask-cors>=4.0
gunicorn>=22.0
requests>=2.31
EOF

# 创建 server.py
cat > server.py << 'EOF'
# -*- coding: utf-8 -*-
"""飞书文档内容获取 HTTP API"""
import os
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from feishu_client import fetch_document

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", f"http://{os.environ.get('PUBLIC_IP', 'localhost')}:5000")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/get_document", methods=["POST"])
def get_document():
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
    return send_from_directory(STATIC_DIR, filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    app.run(host=host, port=port)
EOF

# 创建 feishu_client.py
cat > feishu_client.py << 'EOF'
# -*- coding: utf-8 -*-
import time
import os
import requests
import logging

logger = logging.getLogger(__name__)
BASE_URL = "https://open.feishu.cn/open-apis"
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_token_cache = {}

def _get_token(app_id: str, app_secret: str) -> str:
    cache_key = (app_id, app_secret)
    entry = _token_cache.get(cache_key)
    now = time.time()
    if entry and entry["token"] and now < entry["expire_at"] - 60:
        return entry["token"]
    url = f"{BASE_URL}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=15)
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
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    def _headers(self) -> dict:
        token = _get_token(self.app_id, self.app_secret)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def get_raw_content(self, document_id: str) -> str:
        url = f"{BASE_URL}/docx/v1/documents/{document_id}/raw_content"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        data = resp.json()
        logger.info(f"raw_content code: {data.get('code')}")
        if data.get("code") != 0:
            raise Exception(f"获取文档内容失败: {data}")
        return data.get("data", {}).get("content", "")

    def get_blocks(self, document_id: str) -> list:
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
        if save_dir is None:
            save_dir = STATIC_DIR
        os.makedirs(save_dir, exist_ok=True)
        url = f"{BASE_URL}/drive/v1/medias/{file_token}/download"
        resp = requests.get(url, headers=self._headers(), timeout=60)
        if resp.status_code != 200:
            raise Exception(f"下载图片失败: status={resp.status_code}")
        content_type = resp.headers.get("Content-Type", "image/png")
        ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
        ext = ext_map.get(content_type, ".png")
        filename = f"{file_token}{ext}"
        filepath = os.path.join(save_dir, filename)
        with open(filepath, "wb") as f:
            f.write(resp.content)
        logger.info(f"Downloaded image: {filepath}")
        return filepath

def fetch_document(app_id: str, app_secret: str, document_id: str, base_url: str = "http://localhost:5000") -> dict:
    client = FeishuClient(app_id, app_secret)
    text = client.get_raw_content(document_id)
    blocks = client.get_blocks(document_id)
    image_tokens = [b.get("image", {}).get("token") for b in blocks
                    if b.get("block_type") == 27 and b.get("image", {}).get("token")]
    image_urls = []
    for token in image_tokens:
        try:
            local_path = client.download_image(token)
            filename = os.path.basename(local_path)
            image_url = f"{base_url.rstrip('/')}/static/{filename}"
            image_urls.append(image_url)
        except Exception as e:
            logger.error(f"下载图片失败 token={token}: {e}")
            image_urls.append({"file_token": token, "error": str(e)})
    return {"text": text, "images": image_urls}
EOF

# 安装依赖
pip3 install -r requirements.txt

# 创建 systemd 服务
cat > /etc/systemd/system/feishu-doc-api.service << EOF
[Unit]
Description=Feishu Document API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/feishu-doc-api
Environment="PUBLIC_IP=$PUBLIC_IP"
Environment="BASE_URL=http://$PUBLIC_IP:5000"
ExecStart=/usr/local/bin/gunicorn -w 2 -b 0.0.0.0:5000 --timeout 120 server:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 防火墙放行 5000 端口
if command -v ufw &> /dev/null; then
    ufw allow 5000/tcp || true
fi
if command -v firewall-cmd &> /dev/null; then
    firewall-cmd --permanent --add-port=5000/tcp || true
    firewall-cmd --reload || true
fi

# 启动服务
systemctl daemon-reload
systemctl enable feishu-doc-api
systemctl restart feishu-doc-api

echo ""
echo "=== 部署完成 ==="
echo "服务地址: http://$PUBLIC_IP:5000"
echo "健康检查: curl http://$PUBLIC_IP:5000/health"
echo ""
echo "请确保阿里云安全组放行了 5000 端口!"
echo "查看日志: journalctl -u feishu-doc-api -f"
