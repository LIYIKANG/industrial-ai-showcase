# 04 · API 接口

Base URL：`http://127.0.0.1:8848`
交互式文档（自动生成，可直接试调）：<http://127.0.0.1:8848/docs>

所有接口收发 JSON，字符编码 UTF-8。

---

## 4.1 健康检查

### `GET /api/health`

```json
{
  "ok": true,
  "批次数": 3878,
  "源文件": "/Users/.../副本AI 测试模拟数据-20260805(1).xlsx"
}
```

数据未就绪时返回 `503` + `{"ok": false, "error": "..."}`。

---

## 4.2 参数与配置

### `GET /api/params`

前端三个页面的元数据来源，一次拿全。

```json
{
  "params": [
    {"key":"重量kg","label":"重量","unit":"kg","role":"capacity",
     "decimals":1,"default_tol":null,"default_dir":null},
    {"key":"冻力Bloomg","label":"冻力","unit":"Bloom g","role":"limit",
     "decimals":1,"default_tol":5.0,"default_dir":"min"},
    {"key":"水分%","label":"水分","unit":"%","role":"limit",
     "decimals":2,"default_tol":0.3,"default_dir":"max"}
  ],
  "limits": { "水分%": {"lo": 8.0, "hi": 14.0, "enabled": true} },
  "settings": {
    "bloom_tolerance": 5.0, "min_take": 20.0, "max_batches": 0,
    "candidates": 200, "time_limit": 15.0, "worst_grade": "3级"
  },
  "grades": ["1级","2级","3级","4级"],
  "targetable": ["冻力Bloomg","水分%","灰分%","PH值","勃氏粘度mPa/s",
                 "粘度下降%","透过率450%","透过率620%","电导率us/cm","二氧化硫mg/kg"],
  "default_tol": {"冻力Bloomg": 5.0, "水分%": 0.3},
  "default_dir": {"冻力Bloomg": "min", "水分%": "max", "PH值": "both"},
  "directions": {"min":"只能高不能低","both":"双向容差","max":"只能低不能高"},
  "material_prefs": {"balanced":"不偏好（批次最少）",
                     "save_high":"保留高冻力料","save_low":"保留低冻力料"},
  "target_col": "冻力Bloomg", "weight_col": "重量kg", "id_col": "数据编号"
}
```

`role` 决定该指标在求解中的作用：

| role | 含义 |
|---|---|
| `capacity` | 库存量 / 订单量，不是化验指标 |
| `limit` | 化验指标：可作逐单目标（见 `targetable`），也受全局上下限 `[lo, hi]` 约束 |
| `grade` | 文本等级，作为候选批次的硬过滤条件 |

### `PUT /api/params`

只需传要改的字段，未传的保持原值。

```json
{
  "limits": { "水分%": {"lo": 9.0, "hi": 13.0, "enabled": true} },
  "settings": { "min_take": 30 }
}
```

返回 `{"ok": true, "limits": {...}, "settings": {...}}`（合并后的完整配置）。

下限大于上限时返回 `400` + `detail`。

### `POST /api/params/reset`

恢复出厂默认，返回完整配置。

---

## 4.3 库存

### `GET /api/inventory`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `page` | int | 1 | 页码 |
| `size` | int | 50 | 每页条数，上限 500 |
| `sort_by` | str | 数据编号 | 排序列名（用原始列名，如 `冻力Bloomg`） |
| `desc` | bool | false | 降序 |
| `grade` | str | — | 按水不溶物等级筛选，如 `2级` |
| `keyword` | str | — | 按数据编号精确查找 |
| `bloom_min` / `bloom_max` | float | — | 冻力区间 |
| `weight_min` / `weight_max` | float | — | 重量区间 |

```json
{
  "total": 3878, "page": 1, "size": 50, "pages": 78,
  "columns": ["数据编号","重量kg","冻力Bloomg","..."],
  "rows": [ {"数据编号":1,"重量kg":216.0,"冻力Bloomg":163.1,"...":"..."} ]
}
```

### `GET /api/dashboard`

数据看板的全部聚合，一次算完一次返回。分段界线取自当前保存的参数设定。

```json
{
  "kpi": {"总批次":3878,"总吨位":1927.9,"冻力中位":214.9,
          "冻力范围":[59.8,315.4],"平均批重kg":497.1},
  "曲线": [{"冻力":210.0,"高端料吨":1002.6,"可带动吨":764.3,"最大可配吨":1767.0}],
  "直方图": [{"起":151.1,"止":160.2,"批次":95,"吨位":45.45}],
  "余量": [{"参数":"水分%","名称":"水分","单位":"%","方向":"max",
            "规格下限":8.0,"规格上限":14.0,"p5":10.36,"p50":11.47,"p95":12.66,
            "下方余量%":39.3,"上方余量%":22.3,"余量%":22.3,"瓶颈侧":"上限","超限批次":0}],
  "等级": [{"等级":"2级","批次":2223,"吨位":1125.34}],
  "分段": {"高冻力料":{"批次":388,"吨位":171.9,"占比":8.9}},
  "界线": {"高":275.0,"低":120.0}
}
```

`余量%` 只算**真正会卡壳的那一侧**（由该指标的 `default_dir` 决定）：
`max` 方向看上限，`min` 方向看下限，`both` 取两侧较紧的。
灰分下限写 0 只是形式，报「下方余量仅 9%」会误导。

### `GET /api/inventory/bloom-bands`

| 参数 | 默认 | 说明 |
|---|---|---|
| `high_from` | 275 | 高冻力料的下界 |
| `low_to` | 120 | 低冻力料的上界 |

返回三段的批次数 / 吨位 / 占比 + 冻力分位数。参数设定页用它做界线的即时预览。
`low_to >= high_from` 时返回 400。

### `GET /api/inventory/stats`

库存总览 + 每项指标的分布（数值型给 min/p5/中位/均值/p95/max/缺失数，等级型给取值分布）。

### `POST /api/inventory/reload`

源 Excel 更新后重新导入，绕过 parquet 缓存。返回 `{"ok": true, "批次数": 3878}`。

---

## 4.4 配料求解

### `POST /api/blend`

**请求**

```json
{
  "orders": [
    {
      "name": "客户A",
      "weight": 800,
      "material_pref": "save_high",
      "targets": {
        "冻力Bloomg":     {"value": 210, "tolerance": 5, "direction": "min"},
        "水分%":          {"value": 12,  "tolerance": 0.5, "direction": "max"},
        "勃氏粘度mPa/s":  {"value": 4.5}
      }
    },
    {
      "name": "客户B",
      "weight": 1200,
      "targets": { "冻力Bloomg": {"value": 260, "tolerance": 4} }
    }
  ],
  "settings": { "min_take": 20, "max_batches": 0, "time_limit": 15 }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `orders[].name` | 否 | 单号，留空自动编号为「订单1」。同一请求内不可重复 |
| `orders[].weight` | **是** | 需求量 kg，`0 < weight ≤ 1000000` |
| `orders[].targets` | **是** | 目标指标字典，至少一项 |
| `targets[].value` | **是** | 目标值 |
| `targets[].tolerance` | 否 | 允许偏差，留空用该指标的默认容差（见 `/api/params` 的 `default_tol`） |
| `targets[].direction` | 否 | 约束方向，留空用该指标的默认方向（见 `default_dir`） |
| `orders[].material_pref` | 否 | 取料偏好，默认 `balanced` |
| `settings` | 否 | **临时覆盖**求解设置，不落盘 |

**约束方向 `direction`** —— 对应客户规格单上的符号：

| 值 | 含义 | 结果区间 |
|---|---|---|
| `min` | 只能高不能低 | `[目标, 目标+容差]` |
| `max` | 只能低不能高 | `[目标−容差, 目标]` |
| `both` | 双向 | `[目标−容差, 目标+容差]` |

默认：冻力 / 透过率450 / 透过率620 是 `min`；水分 / 灰分 / 粘度下降 / 电导率 /
二氧化硫是 `max`；PH / 勃氏粘度是 `both`。

**取料偏好 `material_pref`** —— 同样达标时优先动用哪一端的库存：

| 值 | 含义 |
|---|---|
| `balanced` | 不偏好，只求批次数最少（默认） |
| `save_high` | 保留高冻力料，用贴近目标的中间料 |
| `save_low` | 保留低冻力料，几乎只用目标值以上的料 |

订单数上限 20。`targets` 的 key 必须出自 `/api/params` 返回的 `targetable` 列表：

```
冻力Bloomg · 水分% · 灰分% · PH值 · 勃氏粘度mPa/s · 粘度下降%
透过率450% · 透过率620% · 电导率us/cm · 二氧化硫mg/kg
```

（重量是订单量不是化验指标；水不溶物是文本等级，加权平均没有物理意义 —— 两者都不可作目标。）

**旧格式兼容**

早期只支持冻力的调用方仍可用顶层 `bloom` / `tolerance` 字段，会自动合并进 `targets`：

```json
{"orders": [{"bloom": 210, "weight": 500}]}
```

**成功响应**

```json
{
  "status": "OK",
  "warnings": [],
  "耗时秒": 3.57,
  "候选池": 3784,
  "参与候选": 400,
  "订单数": 2,
  "总批次数": 7,
  "总配料量kg": 5000.0,
  "全部达标": true,
  "共用批次": [
    {"数据编号": 2536, "库存kg": 814.0,
     "被订单取用": ["客户A-220","客户B-180"], "合计取用kg": 800.0}
  ],
  "订单": [
    {
      "单号": "客户A", "取料偏好": "save_high",
      "需求量kg": 3000.0, "实配量kg": 3000.0, "批次数": 4, "全部达标": true,
      "用料": [
        {"数据编号": 2536, "取用kg": 814.0, "库存kg": 814.0,
         "占比": 27.13, "冻力Bloomg": 224.1, "水分%": 11.2}
      ],
      "达成": [
        {"参数":"冻力Bloomg","类型":"target","方向":"min","目标":210.0,"容差":5.0,
         "实际":210.0,"偏差":0.0,"下限":210.0,"上限":215.0,"达标":true,"占容差":0.0},
        {"参数":"水分%","类型":"limit","方向":null,"目标":null,"容差":null,
         "实际":11.331,"偏差":null,"下限":8.0,"上限":14.0,"达标":true,"占容差":null}
      ]
    }
  ]
}
```

`warnings` 可能包含：

- `容差内无可行解，以下为最接近的方案，标红项已超差`
- `达到求解时限 15s，返回的是已找到的最优方案。实测好解通常在前几秒就已确定，
  剩余时间花在证明其最优性上，延长时限一般不会改变结果。`

**无解响应**（HTTP **200**，不是 4xx）

```json
{
  "status": "INFEASIBLE",
  "reason": "订单「客户A」冻力要求 ≥400（400.000 ~ 405.000）超出库存可达区间 [59.8, 315.4]",
  "detail": {"订单": "客户A", "库存最小": 59.8, "库存最大": 315.4}
}
```

> 用 200 是刻意的：规格配不出来是正常业务结果，不是接口调用错误。
> 前端据此渲染引导信息（放宽哪项、差多少），而不是弹报错。

**错误响应**

| 状态码 | 场景 |
|---|---|
| 400 | 单号重复；参数越界（Pydantic 校验） |
| 422 | 请求体结构不合法 |
| 500 | 求解器内部异常（附 detail，同时记录服务端堆栈） |

---

## 4.5 调用示例

```bash
# 单目标
curl -X POST http://127.0.0.1:8848/api/blend \
  -H 'Content-Type: application/json' \
  -d '{"orders":[{"bloom":210,"weight":200}]}'

# 多目标 + 单边约束 + 取料偏好
curl -X POST http://127.0.0.1:8848/api/blend \
  -H 'Content-Type: application/json' \
  -d '{"orders":[{"name":"A","weight":3000,"material_pref":"save_high",
                  "targets":{"冻力Bloomg":{"value":220,"direction":"min"},
                             "水分%":{"value":12,"direction":"max"}}}],
       "settings":{"min_take":50}}'

# 改上下限
curl -X PUT http://127.0.0.1:8848/api/params \
  -H 'Content-Type: application/json' \
  -d '{"limits":{"PH值":{"lo":5.0,"hi":6.2,"enabled":true}}}'
```
