# -*- coding: utf-8 -*-
"""生成程序说明文档（Word）- 含架构亮点红字 + 脚注"""

import lxml.etree as etree
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.part import Part
from docx.opc.packuri import PackURI
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
FOOTNOTES_URI = "/word/footnotes.xml"
FOOTNOTES_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
FOOTNOTES_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"

_fn_root = None
_fn_part_ref = None
_fn_id_counter = [1]


def _get_fn_root(doc):
    global _fn_root, _fn_part_ref
    if _fn_root is not None:
        return _fn_root
    for rel in doc.part.rels.values():
        if rel.reltype == FOOTNOTES_RT:
            _fn_part_ref = rel.target_part
            _fn_root = etree.fromstring(_fn_part_ref.blob)
            return _fn_root
    _fn_root = etree.Element("{%s}footnotes" % W, nsmap={"w": W})
    for ftype, fid in [("separator", "-1"), ("continuationSeparator", "0")]:
        fn = etree.SubElement(_fn_root, "{%s}footnote" % W)
        fn.set("{%s}type" % W, ftype)
        fn.set("{%s}id" % W, fid)
        fp = etree.SubElement(fn, "{%s}p" % W)
        fr = etree.SubElement(fp, "{%s}r" % W)
        tag = "separator" if ftype == "separator" else "continuationSeparator"
        etree.SubElement(fr, "{%s}%s" % (W, tag))
    xml_bytes = etree.tostring(_fn_root, xml_declaration=True, encoding="UTF-8", standalone=True)
    _fn_part_ref = Part(PackURI(FOOTNOTES_URI), FOOTNOTES_CT, xml_bytes, doc.part.package)
    doc.part.relate_to(_fn_part_ref, FOOTNOTES_RT)
    return _fn_root


def _flush_fn_part():
    if _fn_root is not None and _fn_part_ref is not None:
        _fn_part_ref._blob = etree.tostring(
            _fn_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )


def add_footnote(doc, para, note_text):
    fn_root = _get_fn_root(doc)
    fid = _fn_id_counter[0]
    _fn_id_counter[0] += 1

    ref_r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    rs = OxmlElement("w:rStyle")
    rs.set(qn("w:val"), "FootnoteReference")
    rpr.append(rs)
    ref_r.append(rpr)
    fnref = OxmlElement("w:footnoteReference")
    fnref.set(qn("w:id"), str(fid))
    ref_r.append(fnref)
    para._p.append(ref_r)

    fn = etree.SubElement(fn_root, "{%s}footnote" % W)
    fn.set("{%s}id" % W, str(fid))
    fp = etree.SubElement(fn, "{%s}p" % W)
    fpp = etree.SubElement(fp, "{%s}pPr" % W)
    fps = etree.SubElement(fpp, "{%s}pStyle" % W)
    fps.set("{%s}val" % W, "FootnoteText")
    fr = etree.SubElement(fp, "{%s}r" % W)
    frpr = etree.SubElement(fr, "{%s}rPr" % W)
    frrs = etree.SubElement(frpr, "{%s}rStyle" % W)
    frrs.set("{%s}val" % W, "FootnoteReference")
    etree.SubElement(fr, "{%s}footnoteRef" % W)
    tr = etree.SubElement(fp, "{%s}r" % W)
    tt = etree.SubElement(tr, "{%s}t" % W)
    tt.text = " " + note_text
    tt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


RED = RGBColor(0xCC, 0x00, 0x00)
BLUE = RGBColor(0x1F, 0x49, 0x7D)


def add_mixed(doc, segments):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    for text, red, note in segments:
        run = p.add_run(text)
        if red:
            run.font.color.rgb = RED
            run.bold = True
        if note:
            add_footnote(doc, p, note)
    return p


def add_body(doc, text):
    p = doc.add_paragraph(text)
    p.paragraph_format.space_after = Pt(4)
    return p


def set_heading(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    h.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for run in h.runs:
        run.font.color.rgb = BLUE
    return h


def add_table(doc, headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
        for run in hdr_cells[i].paragraphs[0].runs:
            run.bold = True
    for row_data in rows:
        row_cells = table.add_row().cells
        for i, val in enumerate(row_data):
            row_cells[i].text = val
    return table


# =============================================================================
doc = Document()
for section in doc.sections:
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(3)
    section.right_margin = Cm(2.5)

title = doc.add_heading("AI 总务辅助系统 — 程序说明书", 0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
doc.add_paragraph("版本：1.0　　作成日：2026-05-10")
doc.add_paragraph("")

set_heading(doc, "一、系统概要")
add_body(
    doc,
    (
        "本系统面向日本不动产总务业务，将 PDF / 图片格式的合同文件（重要事项说明书、"
        "精算书、请求书等）通过 Claude AI 自动识别关键字段，并写入预设 Excel 模板，"
        "从而替代手工录入操作。\n\n"
        "技术栈：Python 3.12 / FastAPI / Anthropic Claude API / openpyxl / PyMuPDF / SQLite"
    ),
)

set_heading(doc, "二、主要功能")

set_heading(doc, "2.1　重要事项说明书处理（主功能）", level=2)
add_body(
    doc,
    (
        "· 上传 PDF 或图片（JPG / PNG / WEBP 等），AI 自动提取 31 个字段\n"
        "· 支持同时上传多份文件，结果自动合并\n"
        "· 提取结果在页面上以表格形式展示，可逐字段手动修正\n"
        "· 修正后一键重新生成 Excel，无需重新上传文件\n"
        "· 生成的 Excel 保留原模板格式，填写内容以红字标注"
    ),
)

set_heading(doc, "2.2　精算书处理（AD相殺書）", level=2)
add_body(
    doc,
    (
        "· 独立入口页面，使用专属字段映射和 Excel 模板\n"
        "· 处理流程与主功能一致（上传 → AI 提取 → 预览 → 修正 → 下载）"
    ),
)

set_heading(doc, "2.3　请求书处理（AD請求書）", level=2)
add_body(
    doc,
    "· 独立入口页面，对应广告费请求书模板\n· 处理流程与主功能一致",
)

set_heading(doc, "2.4　自定义模板解析（Beta 功能）", level=2)
add_body(
    doc,
    (
        "· 用户上传自己的 Excel 或 Word 模板\n"
        "· AI 自动分析模板结构，推断需要提取的字段列表\n"
        "· 再上传对应 PDF，按推断字段提取并写入用户模板"
    ),
)

set_heading(doc, "2.5　用户账号与权限管理", level=2)
add_body(
    doc,
    (
        "· 登录/注销，Access Token（15分钟）+ Refresh Token（7天 HTTP-only Cookie）双 JWT\n"
        "· 角色分两级：admin（管理员）/ operator（操作员）\n"
        "· 管理员可在后台创建、启用/停用账号，查看操作日志\n"
        "· 连续 5 次登录失败锁定账号 15 分钟（防暴力破解）\n"
        "· 新账号首次登录强制修改密码"
    ),
)

set_heading(doc, "三、目录结构")
add_table(
    doc,
    ["目录 / 文件", "作用"],
    [
        ["app.py", "FastAPI 应用入口，注册路由和中间件"],
        ["core/config.py", "读取 .env 配置，初始化 Claude 客户端"],
        ["core/ai_extractor.py", "调用 Claude API，返回字段 JSON"],
        ["core/pdf_utils.py", "PDF 转图片（2倍缩放 + 对比度增强）"],
        ["core/excel_writer.py", "将提取值按 cell 坐标写入 Excel 模板"],
        ["core/template_analyzer.py", "分析用户上传模板，生成字段候选列表"],
        ["api/routes.py", "重要事项说明书 API（/api/process 等）"],
        ["api/seisansho_routes.py", "精算书 API（/api/seisansho/*）"],
        ["api/seikyusho_routes.py", "请求书 API（/api/seikyusho/*）"],
        ["api/beta_routes.py", "Beta 自定义模板 API（/api/beta/*）"],
        ["auth/", "JWT 认证、用户模型、登录防护、审计日志"],
        ["config/field_mapping.json", "重要事项说明书字段定义（31项）"],
        ["config/seisansho_field_mapping.json", "精算书字段定义"],
        ["config/seikyusho_field_mapping.json", "请求书字段定义"],
        ["data/templates/", "Excel 模板文件"],
        ["templates/", "Jinja2 HTML 页面"],
        ["static/", "前端 JS / CSS"],
        ["scripts/init_db.py", "初始化 SQLite 数据库，创建管理员账号"],
        ["deploy/", "Gunicorn / Nginx / systemd 配置模板"],
    ],
)
doc.add_paragraph("")

set_heading(doc, "四、处理流程")
set_heading(doc, "4.1　主处理流程", level=2)

add_body(doc, "① 用户在浏览器登录，通过 JWT 认证")
add_body(doc, "② 上传 PDF / 图片文件（支持多文件）")

add_mixed(
    doc,
    [
        ("③ 服务端判断文件类型：\n", False, None),
        (
            "   · 有文字层的 PDF → 同时发送图片 + 文本给 Claude（图文双模式）",
            True,
            (
                "常见做法是只发文本或只发图片。本系统同时发送两者："
                "纯文本在表格型 PDF 中列序会错乱（金额归入错误字段），"
                "图片+文本双轨让AI同时获得版式信息与文字内容，"
                "大幅提升表格字段提取的准确率。"
            ),
        ),
        (
            "\n   · 扫描件 PDF → 先转为高清 JPEG，再传给 Claude\n"
            "   · 图片文件 → 直接发给 Claude",
            False,
            None,
        ),
    ],
)

add_body(doc, "④ Claude 返回 JSON，每个字段包含 value（提取值）和 note（置信度说明）")

add_mixed(
    doc,
    [
        ("⑤ 多文件结果合并：", False, None),
        (
            "非空值依次覆盖，且新值为 0 时不覆盖已有非零值",
            True,
            (
                "精算书等文档中，消费税栏常显示为 0 圆。"
                "若简单按非空值覆盖，已正确填入的月额金额会被 0 错误替换。"
                "特殊规则：新值为 0 且已存在非零值时，保留原值不覆盖。"
            ),
        ),
    ],
)

add_body(
    doc,
    (
        "⑥ 用 openpyxl 将字段值按 field_mapping.json 中的 cell 坐标写入 Excel 模板\n"
        "⑦ 生成的文件临时保存，返回 file_id 给前端\n"
        "⑧ 前端展示提取结果表格，用户可手动修改字段值\n"
        "⑨ 点击「重新生成」或「下载」，获取最终 Excel 文件"
    ),
)

set_heading(doc, "4.2　扫描增强处理", level=2)
add_mixed(
    doc,
    [
        (
            "PyMuPDF 将 PDF 页面渲染为 2 倍分辨率 JPEG，再用 Pillow 做对比度增强",
            True,
            (
                "直接发送原始扫描 JPEG 时，AI 对低分辨率或低对比度图像的字符识别率明显下降。"
                "2倍渲染将分辨率翻倍，Pillow 对比度增强使字迹更清晰，"
                "两步叠加对老旧扫描件效果显著。"
            ),
        ),
        ("，提升 AI 对模糊扫描件的识别准确率。", False, None),
    ],
)

set_heading(doc, "4.3　字段映射机制", level=2)
add_mixed(
    doc,
    [
        (
            "field_mapping.json 集中管理字段定义。"
            "每条记录包含 id（程序内部键）、"
            "label（发给 AI 的提示语）、cell（Excel 写入位置）。\n",
            False,
            None,
        ),
        (
            "新增字段只需在 JSON 中添加一条记录，AI 提示词和 Excel 写入逻辑均自动适配，无需改代码。",
            True,
            (
                "传统方案中提示词和写入逻辑分开维护，"
                "改一个字段需同时修改多处代码，容易遗漏。"
                "本系统将字段定义集中到 JSON，"
                "ai_extractor.py 运行时读取 JSON 动态生成提示词，"
                "excel_writer.py 读取同一 JSON 决定写入位置，两侧始终保持同步。"
            ),
        ),
    ],
)

set_heading(doc, "五、认证与安全")
add_body(
    doc,
    (
        "· 双 Token 机制：Access Token 短期有效（15分钟），"
        "Refresh Token 长期有效（7天）且存 HTTP-only Cookie，避免 XSS 读取"
    ),
)
add_mixed(
    doc,
    [
        ("· ", False, None),
        (
            "登出时将 Refresh Token 的 JTI 写入黑名单表，实现即时撤销",
            True,
            (
                "标准 JWT 方案下，登出后 Token 仍可继续使用直到过期。"
                "本系统在登出时将 Token 的唯一标识符（JTI）写入数据库黑名单，"
                "后续请求验证时检查 JTI，"
                "即使 Token 未过期也无法复用，实现真正的即时登出。"
            ),
        ),
    ],
)
add_body(
    doc,
    (
        "· 登录失败 5 次触发账号锁定！15分钟），记录到 login_attempts 表\n"
        "· 所有关键操作写入 audit_logs 表（用户名、动作、IP、时间）\n"
        "· CORS 来源由环境变量 CORS_ORIGINS 控制，生产环境限定具体域名"
    ),
)

set_heading(doc, "六、部署方式")
add_body(
    doc,
    (
        "开发环境：python app.py（uvicorn 热重载）\n\n"
        "生产环境：\n"
        "· Gunicorn + UvicornWorker 启动（超时 300 秒，考虑 AI + PDF 处理时长）\n"
        "· Nginx 反向代理，/static/ 直接由 Nginx 提供，其余转发到 Gunicorn\n"
        "· systemd 管理进程（deploy/pdf-analyzer.service）\n"
        "· 配置通过 .env 文件注入，生产环境读取 .env.production"
    ),
)

_flush_fn_part()
out = r"e:\pdf脚本开发\docs\AI总务辅助系统_程序说明书.docx"
# Use raw path to avoid encoding issues
import os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AI总务辅助系统_程序说明书.docx")
doc.save(out)
print("saved ok: " + out)
