# 企业微信 Bot 本地启动流程书

## 当前结论

本次已经验证成功：

```text
企业微信后台
  -> trycloudflare 公网地址
  -> 本地 FastAPI 服务
  -> GET /wecom/callback URL 验证
  -> 返回 200
```

当前方案是本地开发调试方案。只要使用 `cloudflared tunnel --url http://127.0.0.1:8000`，企业微信的消息就会通过临时公网地址转发到当前这台电脑。

因此，在本地调试阶段，每次要让企业微信 Bot 能正常收消息，都需要：

1. 电脑开机并联网。
2. FastAPI 服务运行中。
3. cloudflared 隧道运行中。
4. 企业微信后台 URL 使用当前 cloudflared 生成的地址。

如果任意一个关闭，企业微信就无法访问本地 Bot。

## 第 1 步：进入项目目录

打开终端：

```bash
cd "/Users/liyikang/Desktop/安必成/wechat_bot_logic_demo"
```

## 第 2 步：设置企业微信环境变量

在启动 FastAPI 的同一个终端里执行：

```bash
export WECOM_CORP_ID="你的企业 CorpID"
export WECOM_TOKEN="企业微信后台 Token"
export WECOM_ENCODING_AES_KEY="企业微信后台 EncodingAESKey"
export WECOM_AGENT_ID="你的自建应用 AgentId"
export WECOM_SECRET="你的自建应用 Secret"
```

注意：

```text
Token 和 EncodingAESKey 必须与企业微信后台接收消息配置页完全一致。
```

如果已经把真实配置写入 `.env`，也可以使用：

```bash
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload --env-file .env
```

## 第 3 步：启动 FastAPI 服务

继续在同一个终端执行：

```bash
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

看到类似输出表示启动成功：

```text
Uvicorn running on http://0.0.0.0:8000
Application startup complete.
```

本地健康检查：

```text
http://127.0.0.1:8000/health
```

正常返回：

```text
ok
```

本地模拟聊天页面：

```text
http://127.0.0.1:8000/debug/chat
```

## 快速重启脚本

项目已提供重启脚本：

```bash
./scripts/restart_server.sh
```

脚本会自动：

1. 停止旧的 uvicorn 服务。
2. 停止占用 `8000` 端口的旧进程。
3. 如果存在 `.env`，自动读取 `.env`。
4. 后台启动 FastAPI。
5. 将日志写入：

```text
logs/uvicorn.log
```

如果当前没有 `.env`，可以先创建：

```bash
cp .env.example .env
```

然后确认 `.env` 里的 Token、EncodingAESKey、CorpID 是最新值，再执行：

```bash
./scripts/restart_server.sh
```

## 第 4 步：启动 cloudflared 隧道

新开第二个终端，执行：

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

成功后会出现类似：

```text
https://permalink-victoria-meanwhile-victor.trycloudflare.com
```

这个地址每次可能变化。以终端实际显示为准。

公网健康检查地址：

```text
https://当前地址/health
```

如果返回：

```text
ok
```

说明公网已经能访问到本地服务。

## 第 5 步：配置企业微信后台 URL

进入企业微信管理后台：

```text
应用管理 -> 自建应用 -> 接收消息/API 接收消息
```

URL 填：

```text
https://当前cloudflared地址/wecom/callback
```

例如：

```text
https://permalink-victoria-meanwhile-victor.trycloudflare.com/wecom/callback
```

Token 填：

```text
与 WECOM_TOKEN 一样
```

EncodingAESKey 填：

```text
与 WECOM_ENCODING_AES_KEY 一样
```

点击保存。

保存成功表示：

```text
GET /wecom/callback URL 验证通过
```

## 第 6 步：在企业微信里发消息测试

在企业微信客户端中打开该自建应用，发送：

```text
LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.79, 1K
```

如果一切正常，Bot 会返回处理结果。

## 第 7 步：查看回调访问日志

浏览器打开：

```text
http://127.0.0.1:8000/debug/callback-events
```

如果 URL 验证成功，会看到：

```text
GET /wecom/callback ... status_code: 200
```

如果用户发消息成功，会看到：

```text
POST /wecom/callback ... status_code: 200
```

日志文件位置：

```text
logs/callback_access_log.json
```

## 常见问题

### 1. 每次都要在电脑上启动吗？

本地调试阶段，是的。

因为当前架构是：

```text
企业微信 -> cloudflared 临时公网地址 -> 你的电脑 -> FastAPI
```

所以你的电脑必须保持运行。

### 2. cloudflared 地址每次会变吗？

使用 quick tunnel 时，通常会变。

每次变更后，都需要到企业微信后台把 URL 改成新的：

```text
https://新的地址/wecom/callback
```

### 3. 不想每次都启动电脑怎么办？

需要把项目部署到长期运行的公网服务器，并绑定正式 HTTPS 域名。

正式架构应该是：

```text
企业微信 -> https://正式域名/wecom/callback -> 公网服务器上的 FastAPI
```

这样就不需要依赖你的本地电脑。

### 4. 本地测试业务逻辑，不想走企业微信怎么办？

使用本地调试页面：

```text
http://127.0.0.1:8000/debug/chat
```

或接口：

```text
POST http://127.0.0.1:8000/debug/wecom
```

这两个不需要 cloudflared，也不需要企业微信后台保存 URL。

## 后续正式上线建议

当业务逻辑确认稳定后，建议部署到：

1. 公司备案域名对应的公网服务器。
2. 云服务器，例如阿里云、腾讯云、华为云等。
3. 使用 Nginx + HTTPS 反向代理到 FastAPI。

企业微信后台最终填写：

```text
https://正式域名/wecom/callback
```
