---
name: "pdf-extractor"
description: "解析复杂的二进制 PDF 文档，高精度提取结构化表格，并进行大文本块分析。"
---
# PDF 提取与分析技能 (PDF Extraction & Analysis)

专门用于从非结构化的二进制便携式文档格式（PDF）文件中，高效抓取表格数据、非结构化文本元数据以及内嵌资产的专项技能。

## 推荐技术栈 (Library Stack)
*   `pypdf`：用于处理通用的文档元数据抓取、PDF 合并拆分、以及基础文本页面切片操作的首选轻量库。
*   `pdfplumber`：用于高精度复杂表格边界识别、物理文本坐标定位分析的**绝对默认核心库**。

## 健壮的数据提取例程
在处理多栏财务报表或论文文本时，务必通过显式设置几何边界坐标来确保提取内容的上下关联性：

```python
import pdfplumber

def extract_safe_text(pdf_path: str) -> str:
    extracted_buffer = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # 开启布局保持模式，防止多栏排版混淆
            text = page.extract_text(layout=True)
            if text:
                extracted_buffer.append(text)
    return "\n--- Page Break ---\n".join(extracted_buffer)