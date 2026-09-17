# MVR废水蒸发 AI软测量与能耗诊断 Demo

这是一个面向客户演示的本地 Streamlit Demo，用于展示 AI 如何辅助 MVR 废水蒸发系统进行单位蒸发电耗预测、结垢/传热衰减风险诊断和运行建议生成。

本项目只实现 AI诊断层 和 运行建议层，不连接 PLC/DCS，不执行真实设备控制。所有建议均为“AI辅助建议”，仅供人工决策参考。

## 功能

- 上传 CSV / Excel 格式的 MVR 运行数据。
- 未上传数据时自动生成模拟样例数据。
- 自动计算单位蒸发电耗：`specific_energy = total_power / condensate_flow`。
- 支持 Linear Regression、Random Forest、PLS Regression 三类轻量模型。
- 输出 MAE、RMSE、R2 模型评价指标。
- 构建 0-100 的 `fouling_risk_index` 结垢风险代理指标。
- 展示能耗趋势、结垢风险趋势、特征重要性和 AI辅助建议。
- 新增“数据认知与模型说明”页面，用热力图、分布图、散点图、相关性图和中文说明帮助客户快速理解数据与模型价值。

## 项目结构

```text
mvr_soft_sensor_demo/
├── app.py
├── requirements.txt
├── README.md
├── data/
│   └── sample_mvr_data.csv
├── src/
│   ├── __init__.py
│   ├── data_generator.py
│   ├── data_loader.py
│   ├── feature_engineering.py
│   ├── model_training.py
│   ├── fouling_risk.py
│   └── recommendation.py
```

## 安装

建议使用 Python 3.10 或更高版本。

```bash
cd mvr_soft_sensor_demo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
streamlit run app.py
```

浏览器打开 Streamlit 提示的本地地址，通常为：

```text
http://localhost:8501
```

## 重新生成样例数据

```bash
python3 -m src.data_generator --output data/sample_mvr_data.csv --rows 720 --seed 42
```

## 数据字段

系统期望的数据字段如下：

```text
timestamp
feed_flow
feed_temp
feed_tds
feed_ph
evap_temp
evap_pressure
compressor_freq
compressor_power
pump_power
condensate_flow
concentrate_flow
total_power
cip_flag
alarm_flag
```

如果上传数据缺少部分字段，系统会尽量按照经验默认值或可推导关系补齐，并在页面给出提示。若数据量不足，页面会提示“当前结果仅用于Demo演示”。

## 结垢风险说明

`fouling_risk_index` 是演示用代理指标，并非真实结垢厚度测量值。当前规则综合考虑：

- 同等进料流量下压缩机功率是否上升。
- 同等进料流量下冷凝水流量是否下降。
- 压缩机频率是否长期偏高。
- 进水 TDS 是否偏高。
- 蒸发压力或真空度波动是否变大。
- 距离上次 CIP 的时间是否较长。
- 是否存在报警标记。

风险等级：

- 0-30：低风险
- 31-60：中风险
- 61-80：中高风险
- 81-100：高风险
