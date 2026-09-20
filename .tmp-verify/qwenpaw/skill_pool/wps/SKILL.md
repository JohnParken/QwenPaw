---
name: wps
description: "当用户需要离线创建、读取或编辑可由 WPS Office 打开的文字文档时使用此技能。输出为 WPS 兼容的 .docx 文件，并且只能使用 Python 标准库和 python-docx；禁止联网、在线查询、远程素材下载及其他文档处理组件。不要用于 WPS 表格、WPS 演示、PDF、旧版 .doc 或 WPS 私有 .wps 格式。"
metadata:
  builtin_skill_version: "1.0"
  qwenpaw:
    emoji: "📝"
    requires: {}
---

# WPS 文字文档离线创作

使用 `python-docx` 在完全离线的环境中创建或编辑 WPS Office 可直接打开的 `.docx` 文档。

## 不可违反的边界

- **禁止联网**：不得搜索网页、调用在线 API、访问云盘、下载字体、模板、图片或其他素材。
- **仅用 `python-docx`**：文档读写、排版和检查只能导入 `docx` 包。可以使用 Python 标准库处理路径、文本和基础数据，但不得使用其他第三方包。
- **禁止安装依赖**：不得运行 `pip install`、`uv add`、`conda install`、`npm install` 或任何下载命令。如果 `python-docx` 不可导入，说明缺少本地依赖并停止。
- **禁止替代工具**：不得调用 LibreOffice、WPS 命令行、Pandoc、Node.js、docx-js、COM、AppleScript、浏览器或在线转换服务。
- **只交付 `.docx`**：`python-docx` 不能生成 WPS 私有 `.wps` 文件，也不能可靠处理旧版 `.doc`。用户说“生成 WPS”时，默认理解为生成可由 WPS Office 打开的 `.docx`。
- **只用本地素材**：仅可插入用户提供或工作区内已有的本地图片；不得为补充素材而联网。没有合适素材时，使用文字、表格和留白完成版式。
- **不伪造来源**：离线输入不足时，保留明确占位符或向用户说明缺少的内容，不得编造事实、数据、引用或图片来源。

## 工作流程

1. 明确文档用途、读者、语言、输出路径和必须包含的内容。未指定时，使用 A4 纵向、中文商务文档风格，并输出 `.docx`。
2. 检查 `python-docx` 是否已在本地可用：

   ```bash
   python3 -c "import docx; print(docx.__version__)"
   ```

   导入失败时立即停止并报告；不要尝试安装。
3. 若编辑现有文档，先用 `Document(path)` 读取，识别现有节、样式、标题、表格、页眉和页脚。尽量沿用原模板，不主动重做整体格式。
4. 使用一段可重复运行的 Python 脚本创建或编辑文档。脚本只能依赖标准库和 `python-docx`。
5. 保存到用户指定位置；未指定时，保存到当前工作区内语义清晰的文件名，例如 `项目报告.docx`。
6. 重新用 `python-docx` 打开输出文件并执行结构检查。修复发现的问题后再交付。

## 新建文档基线

从下面的最小结构开始，根据内容删减或扩展。不要机械保留示例文字。

```python
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


def set_run_font(run, name="宋体", size=Pt(12), bold=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = size
    if bold is not None:
        run.bold = bold


def set_style_font(style, name, size, bold=None):
    style.font.name = name
    style._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    style.font.size = size
    if bold is not None:
        style.font.bold = bold


output = Path("WPS文档.docx")
doc = Document()

section = doc.sections[0]
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(3.0)
section.right_margin = Cm(2.5)

styles = doc.styles
set_style_font(styles["Normal"], "宋体", Pt(12))
set_style_font(styles["Title"], "黑体", Pt(22), True)
set_style_font(styles["Heading 1"], "黑体", Pt(16), True)
set_style_font(styles["Heading 2"], "黑体", Pt(14), True)

title = doc.add_paragraph(style="Title")
title.alignment = WD_ALIGN_PARAGRAPH.CENTER
set_run_font(title.add_run("文档标题"), "黑体", Pt(22), True)

doc.add_heading("一、概述", level=1)
body = doc.add_paragraph()
body.paragraph_format.first_line_indent = Cm(0.74)
body.paragraph_format.line_spacing = 1.5
set_run_font(body.add_run("正文内容。"))

doc.save(output)
```

如果环境中的 `python-docx` 版本不接受某个高级参数，使用该版本公开可用的接口简化实现，不得改用别的组件。

## 排版规则

### 页面与段落

- 默认使用 A4 纵向；只有宽表格或用户明确要求时才使用横向页面。
- 正文默认宋体 12pt，一级标题黑体 16pt，二级标题黑体 14pt；现有模板或用户要求优先。
- 中文字体同时设置 `font.name` 和 `w:eastAsia`，避免 WPS 中回退为不合适的字体。
- 长正文使用 1.5 倍行距或固定值，段前段后保持一致；不要用连续空格或空段落模拟缩进和间距。
- 使用 `first_line_indent` 设置首行缩进，使用段落对齐属性设置居中、左对齐或两端对齐。
- 需要分页时使用 `doc.add_page_break()`，不要堆叠空行。

### 标题与目录

- 使用内置 `Title`、`Heading 1`、`Heading 2` 等样式表达层级，不要只对普通段落做加粗和放大。
- `python-docx` 不能自动计算目录页码。除非用户明确接受打开 WPS 后手动更新域，否则不要承诺已生成可自动更新的完整目录。
- 文档较短时，用清晰的标题层级代替目录。

### 列表

- 优先使用内置 `List Bullet` 和 `List Number` 样式。
- 不要用手工输入的 `•`、`-` 或连续空格伪造列表层级。
- 多级编号若无法在当前模板中稳定保持，应使用简洁的单级列表或明确的标题编号。

### 表格

```python
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def shade_cell(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


table = doc.add_table(rows=1, cols=3)
table.style = "Table Grid"
headers = ["项目", "负责人", "状态"]
for cell, text in zip(table.rows[0].cells, headers):
    cell.text = text
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    shade_cell(cell, "D9EAF7")
    for paragraph in cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in paragraph.runs:
            set_run_font(run, "黑体", Pt(10.5), True)
```

- 表格应有清晰表头、统一对齐方式和可读列宽。
- 单元格文字逐一设置中文字体；不要假定表格会自动继承正文样式。
- 避免过宽表格。内容过多时改为分表、横向节或附录。
- `python-docx` 不负责公式计算；表格中的合计、比例等值应由已有离线输入确定，并在内容中说明口径。

### 图片

```python
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm

paragraph = doc.add_paragraph()
paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
paragraph.add_run().add_picture("本地图片.png", width=Cm(14))
caption = doc.add_paragraph("图 1  图片说明")
caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
```

- 插入前确认文件来自本地且路径存在。
- 按版心宽度等比缩放，避免拉伸变形和超出页边距。
- 图片后添加简短图题；来源不明确时不要虚构来源。
- 不得使用 Pillow、Matplotlib 或其他组件生成、处理、裁剪图片。

### 页眉、页脚与页码

- 页眉和页脚可直接通过 `section.header`、`section.footer` 设置。
- 动态页码需要通过 `docx.oxml` 写入 Word 域；这仍属于 `python-docx`，但应保持实现简单，并在保存后重新打开检查域结构。

```python
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, end])


add_page_number(doc.sections[0].footer.paragraphs[0])
```

## 编辑现有文档

- 先创建与源文件不同的输出路径，除非用户明确要求覆盖原文件。
- 修改文字时尽量保留原段落和 run 的格式；如果直接设置 `paragraph.text` 或 `cell.text`，原有 run 级格式可能丢失。
- 对查找替换，先统计匹配数量，再替换并复核。文本可能被拆分到多个 run 中，不能假设每个词都完整位于单个 run。
- 不要承诺保留 `python-docx` 不支持的复杂功能，例如修订跟踪、宏、嵌入对象、SmartArt 或精确浮动版式。
- 对 `.docm`、`.doc`、`.wps` 输入，不转换、不覆盖；说明当前技能仅支持 `.docx`。

## 离线质量检查

必须至少完成以下检查，全部使用 `python-docx`：

```python
from pathlib import Path

from docx import Document


path = Path("WPS文档.docx")
assert path.exists() and path.stat().st_size > 0

check = Document(path)
assert len(check.sections) >= 1
assert any(p.text.strip() for p in check.paragraphs) or len(check.tables) > 0

for table in check.tables:
    assert len(table.rows) > 0
    assert len(table.columns) > 0

print({
    "file": str(path),
    "paragraphs": len(check.paragraphs),
    "tables": len(check.tables),
    "sections": len(check.sections),
})
```

然后人工检查可从文档结构发现的问题：

- 标题是否完整且层级连续；
- 是否存在占位符、重复段落、空白表格或遗漏内容；
- 表格列数是否一致，图片是否全部来自有效本地路径；
- 页面方向和边距是否符合文档用途；
- 输出文件能否被 `Document(output_path)` 再次打开。

受限于“只用 `python-docx`”，不得通过 PDF 转换、截图或其他渲染器做视觉验证。交付时如实说明：已完成离线结构校验，最终分页和字体显示应在用户本机 WPS Office 中确认。

## 交付说明

完成后简要报告：

- `.docx` 文件的绝对路径；
- 新建还是编辑，以及主要内容和版式；
- 已执行的 `python-docx` 离线结构检查；
- 任何需要用户在 WPS Office 中最终确认的分页、字体或域更新事项。
