"""FastAPI 服务入口。

新增企业微信真实回调：
- GET /wecom/callback：企业微信后台 URL 验证
- POST /wecom/callback：接收加密消息、解密文本、调用核心业务逻辑并加密回复

保留 /debug/wecom 用于本地调试，不经过企业微信加解密。
"""

from datetime import datetime
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

from core.processor import process_user_message
from wecom.WXBizMsgCrypt import WXBizMsgCrypt, WXBizMsgCryptErrorCode
from wecom.config import WeComConfig
from wecom.xml_utils import build_text_reply, parse_plain_message


app = FastAPI(title="WeCom Bot Logic Demo")

BASE_DIR = Path(__file__).resolve().parent
CALLBACK_ACCESS_LOG_FILE = BASE_DIR / "logs" / "callback_access_log.json"


DEBUG_CHAT_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>商品报价处理演示</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d8dde6;
      --text: #20242a;
      --muted: #687282;
      --accent: #1769e0;
      --accent-strong: #0f55b8;
      --reply-bg: #eef5ff;
      --input-bg: #fbfcfe;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    main {
      width: min(1180px, calc(100vw - 40px));
      margin: 28px auto;
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(360px, 1fr);
      gap: 18px;
      align-items: stretch;
    }

    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-height: calc(100vh - 56px);
    }

    .composer {
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .conversation {
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }

    .header {
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    h1 {
      margin: 0;
      font-size: 19px;
      font-weight: 650;
    }

    .status {
      min-width: 72px;
      padding: 5px 9px;
      border-radius: 999px;
      background: #e8f6ee;
      color: #177245;
      font-size: 13px;
      text-align: center;
      white-space: nowrap;
    }

    label {
      display: grid;
      gap: 7px;
      color: var(--muted);
      font-size: 13px;
    }

    input,
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--input-bg);
      color: var(--text);
      font: inherit;
      font-size: 15px;
      outline: none;
    }

    input {
      height: 40px;
      padding: 0 11px;
    }

    textarea {
      min-height: 190px;
      resize: vertical;
      padding: 11px;
      line-height: 1.55;
    }

    input:focus,
    textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(23, 105, 224, 0.12);
    }

    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
    }

    button {
      height: 40px;
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 0 14px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }

    .primary {
      background: var(--accent);
      color: white;
    }

    .primary:hover {
      background: var(--accent-strong);
    }

    .secondary {
      background: #fff;
      color: var(--text);
      border-color: var(--line);
    }

    .sample {
      margin: 0;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fafbfc;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .messages {
      padding: 18px;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .empty {
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 18px;
      line-height: 1.7;
      background: #fbfcfe;
    }

    .bubble {
      max-width: 88%;
      padding: 11px 13px;
      border-radius: 8px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border: 1px solid var(--line);
    }

    .user {
      align-self: flex-end;
      background: #f0f3f7;
    }

    .bot {
      align-self: flex-start;
      background: var(--reply-bg);
    }

    .meta {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }

    @media (max-width: 820px) {
      main {
        width: min(100vw - 24px, 720px);
        grid-template-columns: 1fr;
        margin: 12px auto;
      }

      section {
        min-height: auto;
      }

      .conversation {
        min-height: 420px;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class="composer">
      <h1>商品报价处理演示</h1>
      <label>
        模拟用户 ID
        <input id="userId" value="debug_user">
      </label>
      <p style="font-size:13px;color:#718096;line-height:1.8;margin:0">填写商品报价，系统会校验字段并按 SKU 和起订量比较价格。提交只修改此演示副本的产品库。</p>
      <div style="display:flex;gap:6px;flex-wrap:wrap" id="scenario-buttons">
        <button class="secondary" data-example="same">已有商品</button><button class="secondary" data-example="lower">较低报价</button><button class="secondary" data-example="new">新增商品</button><button class="secondary" data-example="missing">缺少字段</button>
      </div><label>
        商品报价（可直接修改）
        <textarea id="content">LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.79, 1K</textarea>
      </label>
      <div class="actions">
        <button class="primary" id="sendBtn" type="button">发送</button>
        <button class="secondary" id="clearBtn" type="button">清空对话</button>
      </div>
      <pre class="sample">固定格式：
SKU, 材质及特殊特性, 硬度, 颜色, 含税单价, MOQ

示例：
LX0105-MTP-15-0, NBR, 60±5°A, 黑色, 1.55, 2000PCS</pre>
    </section>

    <section class="conversation">
      <div class="header">
        <h1>模拟对话</h1>
        <span class="status" id="status">就绪</span>
      </div>
      <div class="messages" id="messages">
        <div class="empty">在左侧输入产品报价文本，点击发送后，这里会显示 Bot 处理结果。</div>
      </div>
    </section>
  </main>

  <script>
    const userIdInput = document.querySelector("#userId");
    const contentInput = document.querySelector("#content");
    const sendBtn = document.querySelector("#sendBtn");
    const clearBtn = document.querySelector("#clearBtn");
    const statusEl = document.querySelector("#status");
    const messagesEl = document.querySelector("#messages");

    function setStatus(text) {
      statusEl.textContent = text;
    }

    function ensureConversation() {
      const empty = messagesEl.querySelector(".empty");
      if (empty) {
        empty.remove();
      }
    }

    function appendMessage(role, text) {
      ensureConversation();
      const bubble = document.createElement("div");
      bubble.className = `bubble ${role}`;

      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = role === "user" ? "模拟用户" : "Bot";

      bubble.appendChild(meta);
      bubble.appendChild(document.createTextNode(text));
      messagesEl.appendChild(bubble);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    async function sendMessage() {
      if (sendBtn.disabled) return;
      const userId = userIdInput.value.trim() || "debug_user";
      const content = contentInput.value.trim();
      if (!content) {
        setStatus("请输入内容");
        return;
      }

      appendMessage("user", content);
      setStatus("发送中");
      sendBtn.disabled = true;

      try {
        const response = await fetch("/debug/wecom", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({user_id: userId, content})
        });

        const data = await response.json();
        if (!response.ok) {
          appendMessage("bot", JSON.stringify(data, null, 2));
          setStatus("失败");
          return;
        }

        appendMessage("bot", data.reply || "");
        setStatus("已回复");
      } catch (error) {
        appendMessage("bot", `请求失败：${error}`);
        setStatus("失败");
      } finally {
        sendBtn.disabled = false;
      }
    }

    document.querySelector('#scenario-buttons').addEventListener('click', event => {
      const key=event.target.dataset.example;if(!key)return;
      const examples={same:'LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.79, 1K',lower:'LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.59, 1K',new:'DEMO-NEW-'+Date.now().toString().slice(-6)+', SILICONE, 50±5°A, 透明, 2.50, 500',missing:'SKU: DEMO-MISSING'};
      contentInput.value=examples[key];contentInput.focus();setStatus('样例已载入');
    });
    sendBtn.addEventListener("click", sendMessage);
    clearBtn.addEventListener("click", () => {
      messagesEl.innerHTML = '<div class="empty">在左侧输入产品报价文本，点击发送后，这里会显示 Bot 处理结果。</div>';
      setStatus("就绪");
    });
    contentInput.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        sendMessage();
      }
    });
  </script>
</body>
</html>
"""


class DebugWeComRequest(BaseModel):
    """本地调试请求体。"""

    content: str
    user_id: str = "debug_user"


def _append_callback_access_log(request, status_code):
    """记录企业微信是否真正请求到了本地回调接口。"""
    CALLBACK_ACCESS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CALLBACK_ACCESS_LOG_FILE.exists():
        try:
            logs = json.loads(CALLBACK_ACCESS_LOG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logs = []
    else:
        logs = []

    logs.append(
        {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query),
            "client": request.client.host if request.client else "",
            "user_agent": request.headers.get("user-agent", ""),
            "status_code": status_code,
        }
    )
    CALLBACK_ACCESS_LOG_FILE.write_text(
        json.dumps(logs[-100:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@app.middleware("http")
async def callback_access_logger(request, call_next):
    """只记录 /wecom/callback 访问情况，方便排查企业微信 URL 验证。"""
    response = await call_next(request)
    if request.url.path == "/wecom/callback":
        _append_callback_access_log(request, response.status_code)
    return response


def _get_wecom_crypt():
    """读取环境变量并初始化企业微信加解密对象。"""
    config = WeComConfig.from_env()
    config.validate_callback_config()
    return config, WXBizMsgCrypt(
        config.token,
        config.encoding_aes_key,
        config.corp_id,
    )


def _raise_wecom_error(stage, ret):
    """把企业微信加解密错误转成 HTTP 400，方便排查。"""
    raise HTTPException(status_code=400, detail=f"{stage} 失败，错误码：{ret}")


@app.get("/health", response_class=PlainTextResponse)
async def health():
    """服务健康检查。"""
    return "ok"


@app.get("/debug/chat", response_class=HTMLResponse)
async def debug_chat():
    """本地模拟企业微信聊天页面。"""
    return DEBUG_CHAT_HTML


@app.post("/debug/wecom")
async def debug_wecom(payload: DebugWeComRequest):
    """本地调试入口：直接把文本交给核心业务逻辑。"""
    reply_text = process_user_message(payload.content, payload.user_id)
    return {
        "user_id": payload.user_id,
        "reply": reply_text,
    }


@app.get("/debug/callback-events")
async def debug_callback_events():
    """查看最近的企业微信回调访问记录。"""
    if not CALLBACK_ACCESS_LOG_FILE.exists():
        return {"events": []}
    try:
        events = json.loads(CALLBACK_ACCESS_LOG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        events = []
    return {"events": events[-20:]}


@app.get("/wecom/callback", response_class=PlainTextResponse)
async def verify_wecom_callback(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企业微信后台保存回调 URL 时，会通过 GET 请求验证 URL。"""
    try:
        _, crypt = _get_wecom_crypt()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    ret, echo_text = crypt.VerifyURL(msg_signature, timestamp, nonce, echostr)
    if ret != WXBizMsgCryptErrorCode.OK:
        _raise_wecom_error("URL 验证", ret)

    return echo_text


@app.post("/wecom/callback")
async def receive_wecom_callback(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """接收企业微信加密消息，解密后调用核心业务逻辑并加密回复。"""
    try:
        config, crypt = _get_wecom_crypt()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    body = (await request.body()).decode("utf-8")
    ret, plain_xml = crypt.DecryptMsg(body, msg_signature, timestamp, nonce)
    if ret != WXBizMsgCryptErrorCode.OK:
        _raise_wecom_error("消息解密", ret)

    try:
        message = parse_plain_message(plain_xml)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail="解密后的 XML 解析失败") from exc

    user_id = message["from_user_name"]
    if message["msg_type"] != "text":
        reply_text = "当前 Demo 暂时只支持文本消息，请发送产品报价文本。"
    else:
        content = message["content"]
        reply_text = process_user_message(content, user_id)

    reply_plain_xml = build_text_reply(
        to_user_name=user_id,
        from_user_name=message["to_user_name"] or config.corp_id,
        content=reply_text,
        agent_id=config.agent_id,
    )
    ret, encrypted_reply = crypt.EncryptMsg(
        reply_plain_xml,
        nonce,
        str(int(time.time())),
    )
    if ret != WXBizMsgCryptErrorCode.OK:
        _raise_wecom_error("消息加密", ret)

    return Response(content=encrypted_reply, media_type="application/xml")
