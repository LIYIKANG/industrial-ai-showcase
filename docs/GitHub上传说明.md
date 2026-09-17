# GitHub 上传说明

本次将桌面“项目演示中心”初始化为 Git 仓库，上传至 `LIYIKANG/industrial-ai-showcase` 私有仓库。

## 上传范围

包括展示中心、8 个子应用、操作指引、Dockerfile、Compose 配置、依赖锁定文件、演示种子、项目资料和检查文档。原业务资料尚未完整脱敏，因此仓库采用私有访问。

`.gitignore` 排除真实环境配置、密钥文件、依赖环境、运行日志、当前会话数据库、上传输出及 Docker 离线镜像归档。`docker/seeds/solver_projects.db` 是演示所需的明确例外，属于原业务种子。

## 配置清理

- PDF 应用的 `.env.example` 清空 API 密钥、JWT 密钥和初始密码；企业微信模板清空企业标识、Token、AES Key、Agent ID 和 Secret。
- 两个 PDF 压测脚本移除写死的账号配置，改为读取 `LOAD_TEST_URL`、`LOAD_TEST_USERNAME`、`LOAD_TEST_PASSWORD`。默认目标为本机 `http://127.0.0.1:18082`，密码默认空值；使用者须自行设置。
- 修改前的模板与脚本仅备份在本机被忽略的 `runtime/github-upload-local-backup/`，不上传至仓库。
- 源代码中没有提交本轮识别出的原凭据；自动扫描不能保证发现所有未知格式的秘密，业务资料脱敏范围见专门清单。

本次配置清理未替换现有 Docker 容器或旧离线镜像。GitHub 源码构建会采用已清理的配置；需要重新分发镜像时应从当前源码重新构建。旧镜像归档不上传本仓库。

## 克隆启动兼容

- 为 `media/` 添加 Git 占位文件，避免空目录在克隆时丢失，导致展示中心挂载静态目录失败。
- Docker 中 pip 的网络读取超时调整为 120 秒、连接重试次数为 5，减少首次下载大型依赖时因短暂网络中断而构建失败的情况。
