# 企业微信回调配置说明

## 本地启动

```bash
cd "/Users/liyikang/Desktop/安必成/wechat_bot_logic_demo"
python3 -m pip install -r requirements.txt

export WECOM_CORP_ID="企业微信 CorpID"
export WECOM_TOKEN="企业微信回调 Token"
export WECOM_ENCODING_AES_KEY="企业微信回调 EncodingAESKey"
export WECOM_AGENT_ID="自建应用 AgentId"
export WECOM_SECRET="自建应用 Secret"

python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

本地调试接口：

```bash
curl -X POST http://127.0.0.1:8000/debug/wecom \
  -H "Content-Type: application/json" \
  -d '{"user_id":"debug_user","content":"LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.79, 1K"}'
```

## 企业微信后台配置

进入企业微信管理后台：

1. 进入「应用管理」
2. 选择你的自建应用
3. 找到「接收消息」或「API 接收消息」
4. URL 填写公网地址，例如：

```text
https://你的公网域名/wecom/callback
```

5. Token 填写同一个 `WECOM_TOKEN`
6. EncodingAESKey 填写同一个 `WECOM_ENCODING_AES_KEY`
7. 保存时企业微信会向 `GET /wecom/callback` 发起 URL 验证

## ngrok 暴露本地服务

```bash
ngrok http 8000
```

拿到类似下面的公网地址：

```text
https://abc123.ngrok-free.app
```

企业微信后台 URL 填：

```text
https://abc123.ngrok-free.app/wecom/callback
```

## cloudflared 暴露本地服务

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

拿到类似下面的公网地址：

```text
https://example.trycloudflare.com
```

企业微信后台 URL 填：

```text
https://example.trycloudflare.com/wecom/callback
```

## 当前回调接口

- `GET /wecom/callback`：企业微信 URL 验证
- `POST /wecom/callback`：接收企业微信加密消息，解密文本后调用 `core.processor.process_user_message(content, user_id)`
- `POST /debug/wecom`：保留给本地调试，不需要企业微信签名和加解密
