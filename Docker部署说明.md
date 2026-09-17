# Docker 版项目演示中心

一个容器同时运行 Agent 体验中心和 8 个应用，按功能分为 7 个可操作 Agent 与 1 个设备运维方案。每个 Agent 有独立介绍和操作指引页。演示账户、本地解析模式和功能边界与桌面版一致。

网页仅提供指定演示样例。原资料与视频仍保留在镜像及源码中，业务数据尚未完成脱敏；内部清单见 `docs/客户信息脱敏清单.md`。

## 在当前电脑启动

启动 Docker Desktop 或 Colima，然后双击 **Docker启动.command**。

也可在终端执行：

```bash
cd "/Users/liyikang/Desktop/项目演示中心"
docker compose up -d --build --wait --wait-timeout 240
```

打开 <http://127.0.0.1:18080>。停止时双击 **Docker停止.command**，或执行：

```bash
docker compose down
```

## 给另一台电脑使用

### 已导出的镜像包

`Docker交付包/` 包含镜像压缩包、无须源码的 Compose 配置和运行说明。安装并启动 Docker 后，在该文件夹运行：

```bash
docker load -i industrial-ai-showcase-1.2.0-linux-arm64.tar.gz
docker compose up -d --wait --wait-timeout 240
```

当前电脑为 Apple Silicon，导出的镜像架构是 **Linux ARM64**，适用于同架构 Docker 环境。普通 Intel / AMD 电脑建议使用下方的源码构建方式，让 Docker 构建对应架构的镜像。

### 从源码构建

完整源码包不包含 Mac 的 `.venv`；Docker 会在镜像内安装 Linux 依赖。

解压 `项目演示中心-Docker源码.tar.gz`，在解压后的项目目录执行：

```bash
docker compose up -d --build --wait --wait-timeout 240
```

默认按当前 Docker 主机架构构建。本次实际构建与运行验证的是 Linux ARM64；AMD64 需要在目标环境重新构建并验证。首次构建需要网络访问镜像仓库、Debian 和 Python 包源。

## 访问地址与端口

入口只有一个：<http://127.0.0.1:18080>。

为保持各原系统的上传、下载、登录会话和 Streamlit WebSocket 正常，容器同时映射连续的 9 个端口。网页自动嵌入子系统，日常操作不需要分别打开它们。

| 端口 | 应用 |
| --- | --- |
| 18080 | 统一展示中心 |
| 18081 | AI 求解器 |
| 18082 | PDF / 订单关键词识别 |
| 18083 | 商品消息与价格处理 |
| 18084 | PDF 历史版本 |
| 18085 | 明胶库存配料 |
| 18086 | 分子量复配 |
| 18087 | MVR 能耗诊断 |
| 18088 | 企业 AI 成熟度诊断 |

默认端口只绑定本机。此配置沿用无需登录的本地演示模式，不是公网发布配置。

若端口被占用，可将 Compose 的映射改为例如 `127.0.0.1:19080-19088:18080-18088`，同时将 `SHOWCASE_PUBLIC_PORT` 改为 `19080`。容器内端口保持不变，然后重新 `docker compose up -d`。

## 数据持久化

命名数据卷 `showcase-data`（实际名称带 Compose 项目前缀）保存：

- 求解器项目、历史版本与输出文件；
- 两套 PDF 系统的独立数据库、模板与导出文件；
- 商品消息演示产品库与操作日志；
- 配料系统参数、缓存和历史回溯结果；
- 展示中心运行日志。

企业成熟度答卷仅保存在浏览器标签页的会话存储中，不上传服务器，也不写入 Docker 数据卷。下载的报告由用户在本机保存。

首次启动初始化示例数据；以后启动只补齐缺失文件，不覆盖已保存的数据。`docker compose down` 保留数据；`docker compose down -v` 会删除数据卷，不应用于普通停止。

容器通过非 root 用户运行。镜像构建排除了密钥配置、桌面会话令牌、旧日志与临时导出；PDF 演示账户和密钥在数据卷内首次启动时生成。

## 可选模型服务

无需模型密钥即可演示企业成熟度评估、数学求解、配料、GPC、MVR、商品消息和带标签的 PDF 样例。

如需配置模型，复制 `docker/env.example` 到项目根目录 `.env`，填写所需项目。然后重新运行 `docker compose up -d`。

- PDF 云端识别：`SHOWCASE_USE_CLOUD=1`，同时配置有效的 `CLAUDE_API_KEY` 与账户可用的 `CLAUDE_MODEL`。
- DeepSeek：配置 `DEEPSEEK_API_KEY`，并在求解器中选择该服务。
- Ollama：默认地址为 `http://host.docker.internal:11434`。模型运行在宿主机，不打包到此镜像中；如果宿主机 Ollama 仅监听 loopback，容器可能无法访问，需要为它配置容器可达的服务地址。

不要把含真实密钥的 `.env` 放进交付包。

## 状态与排查

```bash
docker compose ps
docker compose logs --tail=100
docker compose exec showcase python /app/docker/healthcheck.py
docker compose exec showcase tail -n 80 /data/runtime/solver.log
```

健康检查同时检查 8 个应用；网页左下角可只读查看各应用状态。日志与维护通过以上 Docker 命令处理，避免客户误触管理操作。

使用 `docker compose down` 停止容器。容器配置了自动重启；网页的整体停止 API 不作为 Docker 停止入口。

配置语义可参考 Docker 官方的 [Compose 服务配置](https://docs.docker.com/reference/compose-file/services/) 与 [镜像构建说明](https://docs.docker.com/build/building/best-practices/)。
