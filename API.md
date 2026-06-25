# 飞书文档内容获取 API

**服务地址**: `http://39.106.83.0:5000`

---

## 1. 健康检查

### `GET /health`

**请求示例**:
```bash
curl http://39.106.83.0:5000/health
```

**成功响应** (200):
```json
{"status": "ok"}
```

---

## 2. 获取文档

### `POST /get_document`

获取飞书文档的**纯文本内容**和**图片下载链接**。图片自动下载到服务器，返回可直接访问的公网URL。

**Content-Type**: `application/json`

---

### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `app_id` | string | ✅ | 飞书应用 App ID，格式 `cli_xxxxx` |
| `app_secret` | string | ✅ | 飞书应用 App Secret |
| `document_id` | string | ✅ | 飞书文档 ID，通常在文档URL中 |

> 如何获取：飞书开放平台 → 应用管理 → 凭证与基础信息 → App ID / App Secret

---

### 请求示例

**cURL**:
```bash
curl -X POST http://39.106.83.0:5000/get_document \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "cli_xxxxxxxxxxxxx",
    "app_secret": "xxxxx",
    "document_id": "xxxxxxxxxxxxx"
  }'
```

**Python**:
```python
import requests

resp = requests.post("http://39.106.83.0:5000/get_document", json={
    "app_id": "cli_xxxxxxxxxxxxx",
    "app_secret": "xxxxx",
    "document_id": "xxxxxxxxxxxxx"
})
data = resp.json()
print(data["text"])     # 纯文本内容
print(data["images"])   # 图片URL列表
```

**JavaScript / Node.js**:
```javascript
const resp = await fetch("http://39.106.83.0:5000/get_document", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    app_id: "cli_xxxxxxxxxxxxx",
    app_secret: "xxxxx",
    document_id: "xxxxxxxxxxxxx"
  })
});
const data = await resp.json();
```

---

### 成功响应 (200)

```json
{
  "text": "Cowork v0.3--技能和专家广场 PRD\n\n一、背景和目标\n面向临床医生...",
  "images": [
    "http://39.106.83.0:5000/static/SpMubDNNPo7306xDwk1c8IOznzc.png",
    "http://39.106.83.0:5000/static/MtrDbOP8LoVrj6xix7tc5Vn0nbd.jpg",
    "http://39.106.83.0:5000/static/WQaVbalmDokCetxpN2HcttXlntc.png"
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `text` | string | 文档纯文本内容（飞书 raw_content 返回值） |
| `images` | array[string] | 图片公网可访问 URL 数组，可直接在浏览器打开或作为 `img src` 使用 |

---

### 错误响应

| 状态码 | 响应体 | 原因 |
|--------|--------|------|
| 400 | `{"error": "请求体不是合法 JSON"}` | Content-Type 不是 `application/json` |
| 400 | `{"error": "缺少 app_id 参数"}` | 未传 `app_id` |
| 400 | `{"error": "缺少 app_secret 参数"}` | 未传 `app_secret` |
| 400 | `{"error": "缺少 document_id 参数"}` | 未传 `document_id` |
| 500 | `{"error": "获取 token 失败: ..."}` | app_id / app_secret 无效 |
| 500 | `{"error": "获取文档内容失败: ..."}` | document_id 不存在或无权限 |

---

## 3. 图片展示接口

### `GET /static/{filename}`

获取下载的图片文件，无需认证。

**请求示例**:
```bash
curl -O http://39.106.83.0:5000/static/SpMubDNNPo7306xDwk1c8IOznzc.png
```

**前端使用**:
```html
<img src="http://39.106.83.0:5000/static/SpMubDNNPo7306xDwk1c8IOznzc.png" />
```

---

## 接口汇总

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/get_document` | 获取飞书文档文字+图片 |
| `GET` | `/static/{filename}` | 展示已下载的图片 |

---

## 注意事项

1. **Token 缓存**: 飞书 token 有效期 2 小时，服务端自动缓存刷新
2. **图片持久化**: 首次请求后图片保存在服务器 `static/` 目录，后续重复请求不会重复下载
3. **超时时间**: 单次请求约 5-15 秒（取决于文档大小和图片数量）
4. **并发限制**: gunicorn 默认 2 个 worker，并发请求数有限，可按需调整
