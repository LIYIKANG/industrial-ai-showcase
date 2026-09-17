# EFESO Operations AI Workbench

面向咨询项目的本地优先本体与优化平台。大模型负责理解业务问题和生成标准数学模型，
专业求解器负责数值计算，界面采用 EFESO 风格的橙色、石墨灰和白色工作台设计。

## 架构

```text
前端（vanilla JS + cytoscape.js）
  ── FastAPI（uvicorn）──
      ├─ 本体图谱：networkx（节点 / 边 / 社区 / 密度 / 中心度）
      ├─ 本体导出：GraphML via networkx.write_graphml，Cypher 直出
      ├─ 求解链  ：SciPy HiGHS 严格求解 → PuLP CBC 回退
      ├─ 存储    ：本地 SQLite（项目版本 / 草稿 / 方案基线）
      └─ LLM     ：Ollama 本地 / DeepSeek 云端（统一走 openai SDK）
```

核心链路只复用成熟的开源方案：

| 关注点        | 选型                              | 替换了什么                 |
| ------------- | --------------------------------- | -------------------------- |
| LLM HTTP 客户端 | `openai` SDK（OpenAI 兼容端点） | 自写的 `requests` 封装     |
| 图算法        | `networkx`（密度、度数、DiGraph） | 手写的邻接表与度数计算     |
| GraphML 导出  | `networkx.write_graphml`          | 手写的 XML 字符串拼接      |
| 浏览器图谱渲染 | `cytoscape.js@3.30.2`（CDN）     | 自写的 SVG 节点 / 边绘制   |

页面里**没有构建步骤**——`solver.html` 直接挂载编译产物，所有 Python 包都来自
PyPI，没有自定义 fork。

## 隐私架构

```text
客户问题
  -> Ollama 本地建模（默认）
  -> 模型人工校正与校验
  -> SciPy HiGHS 本地严格求解
  -> PuLP CBC 本地回退
  -> 本地 SQLite 项目版本
```

- 默认模式下，客户文本、数学模型、求解结果和项目历史均不离开电脑。
- 只有主动选择 `DeepSeek API` 时，问题文本会发送到云端。
- DeepSeek Key 只保存在当前浏览器标签页会话，不写入项目数据库。

## 功能

- 任意优化问题的 AI 本体划分和 LP/MILP 建模。
- 可编辑的目标函数、变量、上下界和约束 JSON。
- 求解前结构校验、未知变量检查和边界冲突检查。
- HiGHS 严格求解，CBC 自动回退。
- Graphify 风格的节点、关系、社区、中心节点和置信度图谱。
- 完整实体表、属性表、关系表、变量表、参数表、约束表和建模审计。
- 属性表保留实体属性、变量上下界、目标系数、约束系数和求解假设。
- 关系表区分业务文本抽取关系与模型结构推断关系。
- 后台任务、取消操作和进度显示。
- 本地项目历史版本切换、浏览器草稿自动保存、方案基线和结果对比。
- 图谱邻域模式、全屏模式和本地布局保存。
- 变量图、约束松弛量和 JSON、Excel、GraphML、Cypher 导出。

## 依赖

主要运行时依赖（完整列表见 `pyproject.toml` / `requirements.txt`）：

- Web：`fastapi`、`uvicorn[standard]`、`pydantic`
- LLM：`openai`（同时跑 Ollama 和 DeepSeek 的 OpenAI 兼容端点）
- 数据 / 图：`pandas`、`openpyxl`、`networkx`
- 解析：`pypdf`、`python-docx`、`python-pptx`
- 求解：`scipy`（HiGHS）、`pulp`（CBC）、`numpy`

可选开发依赖（在 `pyproject.toml` 的 `dev` extra 中）：

- `pytest>=8.0.0` —— 运行 `tests/` 下的单元测试
- `ruff>=0.5.0` —— 行长 120，针对 Python 3.10+ 的 lint / format

环境变量集中在 `.env.example`：

- `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_TIMEOUT` / `OLLAMA_STATUS_TIMEOUT`
- `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` / `DEEPSEEK_TIMEOUT` / `DEEPSEEK_STATUS_TIMEOUT`
- `DEEPSEEK_API_KEY`（默认留空，UI 也可以在浏览器里临时输入）

## 运行

```powershell
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000/solver
```

推荐使用仓库自带的 `start_workbench_8001.bat`：

- 自动挑选 Python（`where python` 优先，否则用 `pythoncore-3.14-64`）
- 杀掉占用 0.0.0.0:8000 的旧进程，避免和隔壁 `TPM_Exam` 撞端口
- 跑在 8001 上，访问 `http://127.0.0.1:8001/solver`

Ollama：

```powershell
ollama serve
ollama pull qwen2.5:7b
```

测试：

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests/ -v
```

## 求解器边界

当前严格求解支持结构化线性规划和混合整数线性规划。非线性、复杂车辆路径和
约束规划问题会在校验阶段标记为不可直接求解，而不会让大模型伪造“最优解”。

## 本轮清理

为减少重复造轮子，本轮把以下旧文件移除（如果需要回滚，参考
`backend/core/ontology_graph.py` 等位置的注释或重新引入）：

- `backend/core/case_extractor.py`
- `backend/core/ontology.py`
- `backend/core/solver.py`
- `backend/core/default_data.py`
- `backend/services/ollama_client.py`
- `frontend/index.html`

对应的职责分别被 `openai` SDK、`networkx`、成熟的 `pytest` 测试夹具和
`cytoscape.js` 取代。同步清理了所有 `__pycache__/` 目录。

## 当前边界与后续扩展

- 已完成：本体明细恢复、EFESO 风格、Ollama/DeepSeek 切换、本地 HiGHS/CBC、
  Graphify 式图谱、版本管理、草稿恢复和多格式导出。
- 可继续扩展：CP-SAT、车辆路径专用求解器、非线性求解器、用户权限和项目级加密。
- 云端模型不是默认路径。咨询客户的敏感数据建议始终使用 Ollama 和本地求解器。
