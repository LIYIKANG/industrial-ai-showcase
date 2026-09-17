"""Write the customer-facing, capability-based catalog; service order fixes ports."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
catalog = json.loads((root / 'catalog.json').read_text())

def step(title, action, expected):
    return {'title': title, 'action': action, 'expected': expected}

agents = [
    dict(id='decision', name='经营决策优化 Agent', en='DECISION OPTIMIZATION', icon='◈', color='#315dec',
         services=['solver'], duration='约 3 分钟', audience='计划、运营与供应链人员',
         description='把产能和资源限制变成可计算的条件，找到可执行的生产方案。',
         problem='订单、库存与产能相互制约，手工比较方案费时，容易遗漏限制。',
         outcome='一份包含目标值、产品产量和资源余量的计算结果。',
         prepare='首次体验直接使用内置示例模型，无需上传文件。',
         boundary='示例通过本地数学优化求解；自由文本 AI 建模需要另行配置模型服务。',
         tags=['生产计划', '资源约束', '方案比较'],
         steps=[step('加载示例模型', '在系统顶部点击“加载示例模型”，等待模型加载完成。', '界面出现示例业务问题、产品及资源约束。'),
                step('计算最优方案', '在求解区域点击“计算最优方案”，等待结果返回。', '默认示例显示“目标值 2360”，并列出决策变量和约束结果。'),
                step('检查方案依据', '向下查看产量、资源用量及剩余量，核对哪些约束限制了产出。', '可以解释方案用了哪些资源，以及瓶颈在哪里。'),
                step('调整并再次求解', '点击“数学模型”页签，在 JSON 中修改一个资源约束的 rhs（上限），点击“应用修改”完成校验，再点击“计算最优方案”。', '计算结果随当前模型更新；若条件无法满足，系统会提示不可行。')]),
    dict(id='document', name='单据识别与录入 Agent', en='DOCUMENT EXTRACTION', icon='▤', color='#ca7536',
         services=['pdf', 'pdf-backup'], duration='约 3 分钟', audience='采购、业务与单据录入人员',
         description='将 PDF 单据中的产品、数量和价格整理成可校对、可导出的表格。',
         problem='单据字段分散，重复录入耗时，也容易漏掉数量与价格信息。',
         outcome='经人工核对的结构化产品字段，以及可下载的 Excel 文件。',
         prepare='点击系统内“载入演示样例 PDF”即可开始，也可在演示资料中下载样例。',
         boundary='默认解析 PDF 文字层和配置关键词；扫描件和复杂版式的 AI 识别需要配置云端模型。',
         tags=['PDF 提取', '人工校对', 'Excel / Word'],
         variants={'pdf':'标准体验 · Excel 导出', 'pdf-backup':'扩展体验 · Word 导出'},
         variantNote='两个版本分别保留自己的输入和结果。切换版本后，请在该版本重新载入示例并识别。',
         steps=[step('载入示例单据', '点击“载入演示样例 PDF”，确认上传区域显示样例文件。', '出现一份可直接识别的演示 PDF。'),
                step('开始识别', '点击“开始识别”，等待结果出现。', '出现“SKU 价格明细”和提取的产品字段。'),
                step('人工核对字段', '对照示例单据逐项核对 SKU、数量和价格，在结果输入框中修正需要调整的值。', '结果中的字段与当前校对内容一致。'),
                step('导出本次结果', '点击“下载结果”保存 Excel；修改过字段时，系统会先重新校验再导出。需要 Word 时，切换“扩展体验”，重新识别后点击“下载 Word”。', '浏览器下载本次结果文件；切换版本不会自动搬运已有结果。')]),
    dict(id='quotation', name='商品报价校验 Agent', en='QUOTATION VALIDATION', icon='⇄', color='#ad633e',
         services=['bot'], duration='约 2 分钟', audience='采购与商品运营人员',
         description='把商品消息拆成业务字段，检查报价条件并反馈处理结果。',
         problem='商品消息的字段和报价规则不统一，人工逐条核对容易遗漏。',
         outcome='字段校验、价格比较或产品处理结果，以及需补充信息的提示。',
         prepare='使用系统中的消息示例；推荐先体验“缺少字段”场景。',
         boundary='这是本地消息处理演示，没有向企业微信发送消息；有效报价可能更新演示产品库。',
         tags=['消息解析', '字段检查', '报价规则'],
         steps=[step('选择消息示例', '点击“缺少字段”示例，将示例内容带入输入框。', '输入框出现一条故意缺少必填字段的商品消息。'),
                step('提交并查看提示', '点击“发送”，阅读返回的校验结果。', '出现“字段校验失败”及需补充字段说明。'),
                step('体验完整报价', '选择其他完整报价示例，阅读输入内容后点击“发送”。', '系统根据当前演示产品库执行比较或建档；重复提交时结果可能不同。'),
                step('核对业务处理', '在返回消息中核对 SKU、价格、MOQ 和处理结果，再修改一项信息重新发送。', '反馈对应本次输入，而不是只显示固定成功提示。')]),
    dict(id='formulation', name='库存配料优化 Agent', en='INVENTORY FORMULATION', icon='⌘', color='#238773',
         services=['blend'], duration='约 4 分钟', audience='生产配料与计划人员',
         description='根据目标规格与现有库存，计算各批原料应取用的数量。',
         problem='不同批次指标有差异，人工凑配费时，难同时兼顾规格和库存。',
         outcome='满足订单条件的批次用量、指标核验与可下载的配料清单。',
         prepare='推荐用内置的 3,000 kg / 210 Bloom 示例订单开始。',
         boundary='当前库存与历史回溯沿用项目样例；成本曲线含演示占位价格，不能直接作为采购结算依据。',
         tags=['库存利用', '多指标约束', '配料清单'],
         steps=[step('进入配料求解', '在系统导航中点击“配料求解”，查看订单参数区。', '出现重量、冻力等订单输入项。'),
                step('载入演示订单', '点击“载入演示订单”，确认重量为 3000 kg、冻力为 210 Bloom。', '示例订单填入输入区，可继续手动调整。'),
                step('生成配料方案', '点击“生成配料方案”，等待求解完成。', '默认示例提示“求解成功，全部订单达标”，并列出批次与取用量。'),
                step('导出或调整规格', '点击“导出 CSV”保存清单；修改重量或规格后，再次点击“生成配料方案”。', '参数改变后旧结果和导出入口失效，重新计算后显示新方案。')]),
    dict(id='molecular', name='分子量复配分析 Agent', en='MOLECULAR ANALYSIS', icon='◌', color='#328d91',
         services=['molecular'], duration='约 3 分钟', audience='研发与质量分析人员',
         description='根据分布和配方参数计算分子量，并比较不同批次的复配结果。',
         problem='不同测量方法和批次配比影响分子量，需要统一的计算与比较入口。',
         outcome='GPC 分子量指标、黏度估算或复配指标与趋势。',
         prepare='保留默认 GPC 样例数据即可计算，无需准备检测文件。',
         boundary='结果依据输入参数和公式；页面中的问答为规则引擎，不替代检测结论。',
         tags=['GPC 分析', '黏度估算', '复配计算'],
         steps=[step('选择计算模式', '在“选择计算模式”中保留“GPC分子量计算”，查看默认输入表。', '看到分子数量、分子量和峰面积等样例输入。'),
                step('计算分子量', '点击“计算 GPC 分子量”，查看“结果展示”。', '默认样例的 Mw 为 55,000.00，同时展示其他计算指标。'),
                step('修改输入再计算', '修改表格中一个数值，提交单元格编辑后，再次点击“计算 GPC 分子量”。', '结果与修改后的数据对应；无效数据会给出提示。'),
                step('体验复配计算', '将模式切换为“复配Mw计算”，调整批次重量或固含量，查看复配结果。', '复配结果随已提交的参数变化自动刷新。')]),
    dict(id='energy', name='能耗与设备诊断 Agent', en='ENERGY DIAGNOSTICS', icon='≈', color='#7770c7',
         services=['mvr'], duration='约 4 分钟', audience='工艺、能耗与设备管理人员',
         description='用运行数据预测蒸发电耗，查看影响因素与结垢风险趋势。',
         problem='能耗随工况变化，需要区分正常波动和需要关注的风险。',
         outcome='能耗预测误差、关键影响因素、风险趋势和运行建议。',
         prepare='保持默认 720 行模拟数据，不上传文件也能体验。',
         boundary='使用模拟数据和风险代理指标，只提供辅助分析，没有连接或控制真实设备。',
         tags=['能耗预测', '模型比较', '风险趋势'],
         steps=[step('查看模拟工况', '保持默认数据设置，在“运行诊断总览”查看运行状态和趋势。', '显示 720 行模拟数据及对应的运行图表。'),
                step('选择并训练模型', '在“选择模型”中选择 Linear Regression，然后点击“训练模型”。', '出现“Linear Regression 预测效果”以及 MAE 等误差指标。'),
                step('检查影响与风险', '向下查看“能耗影响因素分析”“结垢风险分析”和“AI运行建议”。', '可以对照运行趋势解释诊断建议，而非直接执行设备操作。'),
                step('理解数据与模型', '切换顶部“数据认知与模型说明”；如修改数据或模型，再回到总览重新训练。', '查看字段含义与模型用途；修改设置后不会沿用旧指标。')]),
    dict(id='maintenance', name='设备远程运维 Agent', en='REMOTE MAINTENANCE', icon='◇', color='#71837d',
         services=[], duration='方案介绍', audience='设备与运维管理人员',
         description='规划设备数据采集、远程监控、异常预警和分级运维流程。',
         problem='多基地设备的运行、报警与维保信息分散，难统一跟踪。',
         outcome='了解数据接入、监控预警与工艺优化的建设路径。',
         prepare='当前提供整理后的能力方案说明，没有可操作程序。',
         boundary='方案阶段，尚未提供设备接入或控制系统。',
         tags=['设备互联', '远程运维', '方案阶段'],
         steps=[step('接入设备数据', '先梳理温度、压力、流量、产量及报警等信号和采集方式。', '规划统一的设备台账与数据基础。'),
                step('建立监控与预警', '规划运行趋势、历史回溯、报警分级和异常诊断。', '确定运维人员发现问题后的排查路径。'),
                step('设计处理流程', '规划预警确认、任务分派、处理记录、权限与操作审计。', '保留现场联锁控制，明确远程操作边界。'),
                step('分阶段验证', '从试点线的数据接入开始，依次验证监控、预警及优化能力。', '按阶段验收后，再考虑多基地复制。')])
]

agents.insert(0, dict(
    id='maturity', name='企业 AI 成熟度诊断 Agent', en='ENTERPRISE AI READINESS',
    icon='◎', color='#416fab', services=['maturity'], duration='约 5 分钟',
    audience='企业负责人、业务与数字化管理人员',
    description='评估数据、系统、管理与组织基础，定位 AI 就绪度，找到优先改善场景。',
    problem='企业希望推进 AI，却不清楚现有基础、关键短板以及应该从哪个业务环节开始。',
    outcome='综合评分、五维雷达图、差距提示、推荐场景和可下载的完整诊断报告。',
    prepare='可以直接开始评估；首次体验也可载入虚构示例答卷，无需填写公司或联系方式。',
    boundary='沿用官网 AI Readiness 的本地题库与评分规则。行业基准为经验估计；没有调用大模型或进行专家现场诊断。',
    tags=['成熟度评估', '短板分析', '场景建议'],
    steps=[
        step('选择开始方式', '点击“开始企业评估”自行作答，或点击“载入示例答卷”先体验报告。', '进入行业选择；示例方式会直接进入答卷核对页，并标记为虚构示例。'),
        step('填写企业画像与现状', '依次选择行业、规模及最多 3 项痛点，完成对应追问和 9 道基础题；选择后点击“下一步”。', '答卷涵盖数据、系统、管理与组织；答案可返回修改，当前标签页内刷新可继续。'),
        step('核对并生成诊断', '选择优先改善环节，在“核对本次答卷”检查答案，点击“生成诊断报告”。', '生成成熟度得分、五维雷达图、需要核对的问题和推荐场景；虚构示例为 49 分。'),
        step('保存报告或调整答案', '点击“下载完整报告”保存可离线打开的 HTML，或“打印 / 保存 PDF”。也可修改答卷重新生成；交给下一位客户前清空本次评估。', '下载内容包含本次答案和评分。修改后重新计算，不沿用旧报告；清空前会要求确认。')]))

if not any(s['id'] == 'maturity' for s in catalog['services']):
    # Append to preserve all existing container ports.
    catalog['services'].append(dict(id='maturity', name='企业 AI 成熟度诊断',
        group='maturity', cwd='projects/maturity', kind='uvicorn',
        entry='showcase_app:app', path='/', health='/health'))

mapping = {sid: a['id'] for a in agents for sid in a['services']}
names = {'solver':'经营决策优化', 'pdf':'单据识别 · Excel', 'pdf-backup':'单据识别 · Word',
         'bot':'商品报价校验', 'blend':'库存配料优化', 'molecular':'分子量复配分析', 'mvr':'能耗与设备诊断', 'maturity':'企业 AI 成熟度诊断'}
for s in catalog['services']:
    s['group'] = mapping[s['id']]
    s['name'] = names[s['id']]
catalog.pop('groups', None)
catalog['agents'] = agents
(root / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
