# 明胶分子量复配计算与 AI 辅助决策 Demo

## 一、项目目录

```text
Evaluation/
├── app.py                 # Streamlit 页面入口
├── calculator.py          # 分子量与复配公式计算模块
├── tests.py               # 基础公式测试
├── requirements.txt       # Python 依赖清单
├── start_app.command      # macOS 一键启动文件
└── README.md              # 本说明文件
```

## 二、推荐启动方式：双击启动

适用于 macOS。

1. 打开项目文件夹 `Evaluation`。
2. 双击 `start_app.command`。
3. 第一次启动时，程序会自动：
   - 创建项目独立的 Python 环境 `.venv`；
   - 安装 Streamlit、pandas 等依赖；
   - 启动 Demo 页面。
4. 浏览器通常会自动打开：

```text
http://localhost:8501
```

如果 macOS 首次阻止打开，可以右键 `start_app.command`，选择“打开”。

## 三、使用终端启动

### 方法 A：使用一键启动文件

打开“终端”，进入项目目录：

```bash
cd "/Users/liyikang/Desktop/添正生物/Evaluation"
```

执行：

```bash
./start_app.command
```

### 方法 B：手动安装并启动

进入项目目录：

```bash
cd "/Users/liyikang/Desktop/添正生物/Evaluation"
```

创建 Python 独立环境：

```bash
python3 -m venv .venv
```

激活环境：

```bash
source .venv/bin/activate
```

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

启动页面：

```bash
python3 -m streamlit run app.py
```

## 四、停止页面

在正在运行程序的终端窗口中按：

```text
Control + C
```

## 五、重新启动

以后再次使用时，直接双击 `start_app.command`，或者执行：

```bash
cd "/Users/liyikang/Desktop/添正生物/Evaluation"
./start_app.command
```

## 六、运行公式测试

执行：

```bash
cd "/Users/liyikang/Desktop/添正生物/Evaluation"
python3 tests.py
```

正常情况下会显示：

```text
案例1通过：GPC 计算结果正确
案例2通过：基础复配 Mw = 62500
案例3通过：固含量修正后 Mw = 80000
全部测试通过！
```

## 七、常见问题

### 1. 提示 `command not found: streamlit`

请不要直接输入 `streamlit run app.py`，改用：

```bash
python3 -m streamlit run app.py
```

或者直接运行：

```bash
./start_app.command
```

### 2. 提示端口 8501 已被占用

通常表示页面已经启动。直接在浏览器访问：

```text
http://localhost:8501
```

也可以换一个端口启动：

```bash
python3 -m streamlit run app.py --server.port 8502
```

然后访问：

```text
http://localhost:8502
```

### 3. 页面没有自动打开

确认终端没有报错，然后手动在浏览器中输入：

```text
http://localhost:8501
```

### 4. 修改代码后页面没有变化

保存代码，Streamlit 通常会自动刷新。也可以在浏览器中刷新页面。

