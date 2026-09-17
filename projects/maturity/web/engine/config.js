/**
 * AI Readiness 快评 —— 题库与规则配置的唯一数据源。
 *
 * 流程：行业 → 规模 → 痛点(≤3) → 痛点追问(选 N 项出 N 题) → 评估题 → 快赢环节 → 报告。
 * 计分沿 4 个维度（D 数据 / S 系统 / M 管理 / O 组织），
 * 雷达图展示 5 轴 —— 第 5 轴「AI 行动力」由组织维度中标记 ai 的题单独抽出。
 */
export const DIMS = {
    D: { name: '数据基础', short: '数据' },
    S: { name: '系统能力', short: '系统' },
    M: { name: '管理成熟度', short: '管理' },
    O: { name: '组织与人', short: '组织' },
};
/** 总分中各维度的权重（和为 1） */
export const DIM_WEIGHTS = {
    D: 0.3,
    S: 0.25,
    M: 0.25,
    O: 0.2,
};
export const AI_AXIS_NAME = 'AI 行动力';
/** 关键词按数组顺序优先匹配（先具体行业，后宽泛行业） */
export const INDUSTRIES = [
    {
        id: 'auto',
        name: '汽车及零部件',
        keywords: ['汽车', '整车', '零部件', '汽配', '车灯', '座椅', '底盘', '动力电池', '新能源车'],
    },
    {
        id: 'pharma',
        name: '生物医药与大健康',
        keywords: ['医药', '制药', '药业', '生物', '医疗', '器械', '疫苗', '诊断', '保健', '大健康'],
    },
    {
        id: 'fmcg',
        name: '食品饮料与快消',
        keywords: ['食品', '饮料', '乳', '酒', '调味', '烘焙', '零食', '日化', '洗护', '快消'],
    },
    {
        id: 'electronics',
        name: '电子电器与消费电子',
        keywords: ['电子', '电器', '家电', '半导体', '芯片', 'PCB', '显示', '光电', '元器件', '电池'],
    },
    {
        id: 'machinery',
        name: '机械装备与通用设备',
        keywords: ['机械', '装备', '设备', '机床', '泵', '阀', '模具', '液压', '注塑', '自动化'],
    },
    {
        id: 'chem',
        name: '化工与流程工业',
        keywords: ['化工', '化学', '材料', '冶金', '钢铁', '建材', '水泥', '造纸', '涂料', '塑料', '橡胶', '石化', '玻璃', '陶瓷'],
    },
    {
        id: 'other',
        name: '其他行业',
        keywords: [],
    },
];
export const INDUSTRY_NAME = Object.fromEntries(INDUSTRIES.map((i) => [i.id, i.name]));
export const SIZES = [
    { id: 's1', label: '50 人以下' },
    { id: 's2', label: '50 – 200 人' },
    { id: 's3', label: '200 – 1000 人' },
    { id: 's4', label: '1000 人以上' },
];
export const PAINS = [
    {
        id: 'p_delivery',
        label: '交付常延期，排产靠经验拍',
        followUp: {
            id: 'f_delivery',
            dim: 'M',
            text: '目前的生产排产计划主要怎么定？',
            options: [
                { label: '基本靠经验与口头协调', score: 0 },
                { label: '以 Excel 排产为主', score: 1 },
                { label: '有系统排产，但经常需要人工调整', score: 2 },
                { label: '以系统排产为主，例外情况才人工干预', score: 3 },
            ],
        },
    },
    {
        id: 'p_quality',
        label: '质量问题反复出现，追溯要翻半天记录',
        followUp: {
            id: 'f_quality',
            dim: 'D',
            text: '出现质量问题时，多快能定位到具体批次与工序？',
            options: [
                { label: '基本查不到源头', score: 0 },
                { label: '翻纸质记录，通常要几天', score: 1 },
                { label: '部分环节可查，大约半天到一天', score: 2 },
                { label: '系统内可以快速追溯', score: 3 },
            ],
        },
    },
    {
        id: 'p_inventory',
        label: '库存压得高，物料齐套还是难',
        followUp: {
            id: 'f_inventory',
            dim: 'S',
            text: '库存与物料数据目前记在哪里？',
            options: [
                { label: '纸质台账或口头传递', score: 0 },
                { label: 'Excel 台账为主', score: 1 },
                { label: 'ERP 里有数，但经常与实物对不上', score: 2 },
                { label: 'ERP 数据基本准确可信', score: 3 },
            ],
        },
    },
    {
        id: 'p_equipment',
        label: '设备突发停机，维护靠事后抢修',
        followUp: {
            id: 'f_equipment',
            dim: 'D',
            text: '设备的运行数据有没有被记录下来？',
            options: [
                { label: '基本没有记录', score: 0 },
                { label: '人工点检、抄表记录', score: 1 },
                { label: '部分关键设备有数据采集', score: 2 },
                { label: '主要设备已联网自动采集', score: 3 },
            ],
        },
    },
    {
        id: 'p_data',
        label: '数据散在各处，报表全靠人工汇总',
        followUp: {
            id: 'f_data',
            dim: 'S',
            text: '各个系统、表格之间的数据目前如何汇总？',
            options: [
                { label: '基本不汇总，各管各的', score: 0 },
                { label: '人工导出后手动拼表', score: 1 },
                { label: '部分数据能自动同步', score: 2 },
                { label: '已有统一的数据平台或数据仓库', score: 3 },
            ],
        },
    },
    {
        id: 'p_knowledge',
        label: '老师傅经验难传承，新人上手慢',
        followUp: {
            id: 'f_knowledge',
            dim: 'O',
            text: '工艺经验与作业标准现在如何沉淀？',
            options: [
                { label: '主要在老师傅脑子里', score: 0 },
                { label: '有文档，但散乱、更新不及时', score: 1 },
                { label: '有体系化文档，但查找不方便', score: 2 },
                { label: '有知识库且保持更新', score: 3 },
            ],
        },
    },
];
export const MAX_PAINS = 3;
/* --------------------------------------------------------------------------
   计分评估题（固定 9 题，覆盖 4 个维度）
   -------------------------------------------------------------------------- */
export const CORE_QUESTIONS = [
    {
        id: 'D1',
        dim: 'D',
        text: '生产与业务数据目前主要以什么方式记录？',
        options: [
            { label: '纸质单据为主', score: 0 },
            { label: 'Excel 表格为主', score: 1 },
            { label: '核心环节进系统，其余靠 Excel', score: 2 },
            { label: '绝大部分业务在系统中流转', score: 3 },
        ],
    },
    {
        id: 'D2',
        dim: 'D',
        text: '这些数据的准确性如何？',
        options: [
            { label: '账实经常不符，月底靠盘点对账', score: 0 },
            { label: '有偏差，需要人工核对修正', score: 1 },
            { label: '大体准确，个别环节有滞后', score: 2 },
            { label: '及时准确，可直接用于决策', score: 3 },
        ],
    },
    {
        id: 'S1',
        dim: 'S',
        text: '公司目前已上线哪些核心业务系统？',
        options: [
            { label: '基本没有系统', score: 0 },
            { label: '只有财务或进销存', score: 1 },
            { label: '有 ERP，部分环节有 MES / CRM 等', score: 2 },
            { label: 'ERP、MES 等核心系统齐备', score: 3 },
        ],
    },
    {
        id: 'S2',
        dim: 'S',
        text: '这些系统之间的数据打通程度如何？',
        options: [
            { label: '谈不上打通', score: 0 },
            { label: '各系统独立，靠人工导数', score: 1 },
            { label: '部分系统有接口打通', score: 2 },
            { label: '主要系统已经集成', score: 3 },
        ],
    },
    {
        id: 'M1',
        dim: 'M',
        text: '关键业务流程的标准化程度如何？',
        options: [
            { label: '因人而异，没有统一做法', score: 0 },
            { label: '有制度，但执行看人', score: 1 },
            { label: '主要流程有 SOP 且基本执行', score: 2 },
            { label: '流程标准化，且在持续优化', score: 3 },
        ],
    },
    {
        id: 'M2',
        dim: 'M',
        text: '经营层多快能看到关键经营数据（产量、成本、交付等）？',
        options: [
            { label: '月底出报表才知道', score: 0 },
            { label: '每周人工汇总一次', score: 1 },
            { label: '有看板，但数据有滞后', score: 2 },
            { label: '关键指标实时或当日可见', score: 3 },
        ],
    },
    {
        id: 'O1',
        dim: 'O',
        text: '高层对 AI / 数字化投入的态度是？',
        options: [
            { label: '暂时不在考虑范围', score: 0 },
            { label: '感兴趣，但还没有预算', score: 1 },
            { label: '有预算，在等合适的切入点', score: 2 },
            { label: '已立项或有明确投入计划', score: 3 },
        ],
    },
    {
        id: 'O2',
        dim: 'O',
        ai: true,
        text: '团队目前对 AI 工具的使用情况？',
        options: [
            { label: '基本没有人在用', score: 0 },
            { label: '个别人自发使用', score: 1 },
            { label: '部分部门已日常使用', score: 2 },
            { label: '已有内部推广或统一工具', score: 3 },
        ],
    },
    {
        id: 'O3',
        dim: 'O',
        ai: true,
        text: '公司是否做过 AI / 智能化的尝试？',
        options: [
            { label: '还没有', score: 0 },
            { label: '了解过方案，未落地', score: 1 },
            { label: '做过试点，效果一般', score: 2 },
            { label: '已有落地场景在运行', score: 3 },
        ],
    },
];
export const QUICK_WIN_QUESTION = {
    id: 'QW',
    text: '如果三个月内先见效，您最希望改善哪些环节？',
    hint: '可多选',
    options: [
        { id: 'qw_doc', label: '报价、合同、单据等文档处理' },
        { id: 'qw_report', label: '生产 / 经营报表自动生成' },
        { id: 'qw_qa', label: '售后与客户答复效率' },
        { id: 'qw_trace', label: '质量追溯与异常分析' },
        { id: 'qw_know', label: '知识沉淀与新人培训' },
        { id: 'qw_none', label: '说不上来，想听专业建议', exclusive: true },
    ],
};
export const BENCHMARKS = {
    machinery: { total: 52, dims: { D: 50, S: 52, M: 55, O: 48 }, ai: 42 },
    electronics: { total: 60, dims: { D: 62, S: 63, M: 58, O: 55 }, ai: 52 },
    auto: { total: 62, dims: { D: 63, S: 66, M: 62, O: 55 }, ai: 50 },
    chem: { total: 55, dims: { D: 56, S: 58, M: 55, O: 48 }, ai: 44 },
    fmcg: { total: 54, dims: { D: 55, S: 56, M: 54, O: 50 }, ai: 46 },
    pharma: { total: 58, dims: { D: 58, S: 60, M: 62, O: 52 }, ai: 46 },
    other: { total: 55, dims: { D: 55, S: 56, M: 55, O: 50 }, ai: 46 },
};
/** 正态近似的标准差（行业内总分离散程度的经验值） */
export const BENCHMARK_SIGMA = 12;
/** 按 min 从高到低排列，取第一个满足的 */
export const TIERS = [
    {
        min: 80,
        id: 'leading',
        name: '融合领先期',
        line: '数据、系统与组织基础扎实，适合规模化推进 AI 场景组合，构建长期壁垒。',
    },
    {
        min: 60,
        id: 'advancing',
        name: '价值提升期',
        line: '基础已具备，关键是选对高价值场景，让 AI 在真实业务里跑出可衡量的结果。',
    },
    {
        min: 40,
        id: 'building',
        name: '基础建设期',
        line: '部分环节已数字化，建议以小场景验证带动数据与流程补课，边见效边打基础。',
    },
    {
        min: 0,
        id: 'starting',
        name: '探索起步期',
        line: '当前更适合从单点提效切入，用轻量工具先见效，再逐步补齐数据与系统基础。',
    },
];
export const CARDS = {
    card_report: {
        id: 'card_report',
        title: '经营数据看板与报表自动化',
        desc: '打通现有系统与台账数据，自动生成生产与经营报表，关键指标当日可见。',
        effect: '报表整理工作量预计减少 70% 以上，经营层告别月底才看数。',
        cycle: '4 – 6 周可上线首版',
        prereq: '核心单据与台账已有电子化记录',
        prereqHint: '建议先完成关键单据的电子化录入，再接自动汇总',
        expert: '多数企业不缺数据，缺的是把数据按同一口径拉通的那一步。先让老板每天看到真数，是所有 AI 场景的起点。',
    },
    card_know: {
        id: 'card_know',
        title: '企业知识库与智能问答',
        desc: '把工艺文件、制度与历史项目沉淀为可检索的知识库，新人与一线随问随答。',
        effect: '专家重复答疑时间明显释放，新人上手周期预计缩短一半。',
        cycle: '4 – 8 周可上线首版',
        prereq: '存量文档与制度资料可以收集整理',
        prereqHint: '建议先做一轮专家访谈与文档补录，把口头经验写下来',
        expert: '经验传承的关键不是"留住人"，而是把人脑里的判断变成组织资产。知识库是投入产出比最稳的第一步。',
    },
    card_trace: {
        id: 'card_trace',
        title: '质量追溯与异常分析助手',
        desc: '结构化质检与工艺记录，异常发生时自动关联批次、工序与参数，生成追溯初稿。',
        effect: '单次异常追溯从数天缩短到小时级，质量会议有据可依。',
        cycle: '6 – 10 周可上线首版',
        prereq: '质检与生产记录可以电子化获取',
        prereqHint: '建议先把质检记录从纸质搬进系统或表格，保证可回查',
        expert: '质量问题的成本大头不在报废，而在反复发生。能快速追溯，才谈得上根因分析和预防。',
    },
    card_maint: {
        id: 'card_maint',
        title: '设备数据采集与预警起步包',
        desc: '从关键设备起步做数据采集，建立运行基线与异常预警，替代纯事后抢修。',
        effect: '突发停机的响应从被动转为提前预警，维保安排更有计划性。',
        cycle: '8 – 12 周可跑通首批设备',
        prereq: '关键设备具备数据接口或可加装采集',
        prereqHint: '建议先选 2 – 3 台影响最大的设备做小范围加装验证',
        expert: '预测性维护不必一步到位，先让最疼的几台设备"开口说话"，价值就已经出来了。',
    },
    card_plan: {
        id: 'card_plan',
        title: '排产与物料齐套辅助决策',
        desc: '基于订单、库存与产能数据做齐套风险预警与排产建议，替代纯经验协调。',
        effect: '齐套异常由事后追查转为提前预警，交付承诺更有底气。',
        cycle: '8 – 12 周可上线首版',
        prereq: 'ERP 或台账中的库存数据基本可信',
        prereqHint: '建议先做一轮账实核对与数据治理，把库存数据修准',
        expert: '排产的难点从来不是算法，而是数据可信。数据修准之后，辅助决策往往立竿见影。',
    },
    card_doc: {
        id: 'card_doc',
        title: '单据与文档流程自动化',
        desc: '对报价、合同、对账单等高频文档做智能解析与流转，减少人工录入与核对。',
        effect: '文档类流程处理时间预计缩短 80%，错漏率同步下降。',
        cycle: '3 – 6 周可上线首版',
        prereq: '相关流程相对固定、有明确规则',
        prereqHint: '建议先梳理一条最高频的单据流程作为试点',
        expert: '文档自动化是见效最快的 AI 场景之一，适合作为第一个让团队"眼见为实"的项目。',
    },
};
/* --------------------------------------------------------------------------
   页面文案
   -------------------------------------------------------------------------- */
export const COPY = {
    eyebrow: 'AI Readiness 快评',
    title: '五分钟，看清企业的 AI 就绪度',
    lede: '回答十余道关于数据、系统、管理与组织的问题，即刻获得一份带行业对照的 AI 就绪度报告，以及为您定制的落地场景建议。',
    startAction: '开始快评',
    restartAction: '重新测评',
    timeNote: '约 5 分钟 · 免费 · 即时出报告',
    disclaimer: '本报告由快评作答自动生成，行业基准为经验估计值，相对位置为近似测算，仅供决策参考；具体路径以现场诊断为准。',
    closing: '以上判断基于本次快评作答自动生成，仅供参考；具体落地路径以现场诊断为准。',
};
