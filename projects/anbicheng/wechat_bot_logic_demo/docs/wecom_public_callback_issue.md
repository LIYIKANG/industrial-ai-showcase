# 企业微信真实回调公网入口问题记录

## 当前问题

企业微信自建应用的真实接收消息回调需要配置：

```text
https://公网可访问域名/wecom/callback
```

本地地址不能直接填写到企业微信后台，例如：

```text
http://127.0.0.1:8000/wecom/callback
```

原因是 `127.0.0.1` 只代表当前机器。企业微信服务器访问这个地址时，访问的是企业微信服务器自己，不是开发电脑。

## 最新状态

已使用 `cloudflared` quick tunnel 完成企业微信 URL 验证。

验证成功时的链路为：

```text
企业微信后台
  -> trycloudflare 公网地址
  -> 本地 FastAPI 服务
  -> GET /wecom/callback
  -> status_code: 200
```

当前可作为本地开发调试方案使用。但 quick tunnel 仍是临时地址，地址可能变化，且需要开发电脑、FastAPI 服务、cloudflared 同时保持运行。

## 当前验证现象

本地 FastAPI 服务可以正常访问：

```text
http://127.0.0.1:8000/health
```

ngrok 公网地址也可以访问到本地服务：

```text
https://snowiness-shelter-antarctic.ngrok-free.dev/health
```

但是企业微信后台保存回调 URL 时提示：

```text
openapi回调地址请求不通过
```

企业微信后台页面同时提示：

```text
需配置备案主体与当前企业主体相同或有关联关系的域名
```

因此目前高度怀疑企业微信后台不接受 `ngrok-free.dev` 这种临时域名作为正式回调地址。

## 结论

如果要在企业微信里直接和自建应用 Bot 对话，必须准备企业微信服务器可访问的公网入口。

公网入口可以是：

```text
https://公司备案域名/wecom/callback
```

或部署到公网服务器后绑定的 HTTPS 域名。

不建议正式使用：

```text
ngrok 临时域名
cloudflared 临时域名
本地 127.0.0.1 地址
```

这些地址适合开发调试，但可能无法通过企业微信后台的域名主体校验。

## 当前继续测试方案

在没有正式公网域名之前，先继续使用本地 debug 接口测试核心业务逻辑：

```text
POST http://127.0.0.1:8000/debug/wecom
```

示例：

```bash
curl -X POST http://127.0.0.1:8000/debug/wecom \
  -H "Content-Type: application/json" \
  -d '{"user_id":"debug_user","content":"LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.79, 1K"}'
```

该接口不经过企业微信加解密，不需要公网域名，适合继续验证解析、查询、价格比较、日志记录等核心业务逻辑。

## 临时内网穿透尝试记录

已安装并尝试 `cloudflared`：

```bash
brew install cloudflared
cloudflared tunnel --url http://127.0.0.1:8000
```

本次临时生成的公网地址为：

```text
https://each-stuff-voluntary-fork.trycloudflare.com
```

测试：

```text
https://each-stuff-voluntary-fork.trycloudflare.com/health
```

可以正常返回：

```text
ok
```

说明 `cloudflared -> 本地 FastAPI` 这条链路是通的。

如果要拿它尝试企业微信后台配置，URL 应填写：

```text
https://each-stuff-voluntary-fork.trycloudflare.com/wecom/callback
```

但它仍然是临时域名，可能和 ngrok 一样无法通过企业微信后台的备案主体校验。

## 后续上线处理

准备正式接入企业微信时，需要：

1. 准备公司备案域名或可被企业微信接受的公网 HTTPS 域名。
2. 将 FastAPI 服务部署到公网服务器，或通过反向代理转发到服务。
3. 企业微信后台 URL 配置为：

```text
https://正式域名/wecom/callback
```

4. Token、EncodingAESKey 与服务环境变量保持完全一致。
5. 保存企业微信后台配置，完成 URL 验证。
