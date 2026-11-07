"""Build paper.docx from main.tex, so the Word and PDF versions share one source.

Handles only the LaTeX this manuscript uses. An unknown command raises an error rather than leaking
into the document. Citation numbers come from main.bbl, so compile the PDF first:

    ../../.tools/tectonic/tectonic.exe --keep-logs --keep-intermediates main.tex
    python build_docx.py

Needs python-docx and PyMuPDF (authoring tools, not runtime requirements).
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "paper.docx"
COLUMN_IN, TEXT_IN = 3.5, 7.16
GREEK = {r"\pi": "π", r"\theta": "θ", r"\times": "×"}
ROMAN = "I II III IV V VI VII VIII IX X XI XII XIII XIV XV".split()
SIMPLE = (("---", "—"), ("--", "–"), ("``", "“"), ("''", "”"), ("~", "\u00a0"), (r"\%", "%"), (r"\_", "_"), (r"\&", "&"), (r"\v{z}", "ž"), (r"\ ", " "))
TOKEN = re.compile(r"(\\textbf\{[^{}]*\}|\\textit\{[^{}]*\}|\\texttt\{[^{}]*\}|\$[^$]+\$)")


def label_numbers(tex: str) -> dict[str, str]:
    """Number figures and tables in source order, as LaTeX does, with Roman numerals for tables."""
    numbers, figures, tables = {}, 0, 0
    for block in re.findall(r"\\begin\{(figure\*?|table\*?)\}(.*?)\\end\{\1\}", tex, re.S):
        kind, body = block
        label = re.search(r"\\label\{([^}]+)\}", body)
        if kind.startswith("figure"):
            figures += 1
            value = str(figures)
        else:
            tables += 1
            value = ROMAN[tables - 1]
        if label:
            numbers[label.group(1)] = value
    return numbers


def citation_numbers(bbl: str) -> dict[str, int]:
    return {key: n for n, key in enumerate(re.findall(r"\\bibitem\{([^}]+)\}", bbl), 1)}


def plain(text: str, cites: dict[str, int], labels: dict[str, str]) -> str:
    """Resolve references and punctuation; leave bold, italic, mono and math tokens for runs()."""
    def cite(match):
        return ", ".join(f"[{cites[key.strip()]}]" for key in match.group(1).split(","))

    text = re.sub(r"\\cite\{([^}]+)\}", cite, text)
    text = re.sub(r"\\ref\{([^}]+)\}", lambda m: labels[m.group(1)], text)
    for old, new in SIMPLE:
        text = text.replace(old, new)
    return text


def math_runs(body: str) -> list[tuple[str, str]]:
    """Split inline math into (kind, text), where kind is 'italic' or 'sub'. Operators stay upright."""
    for old, new in GREEK.items():
        body = body.replace(old, new)
    out = []
    for piece in re.split(r"(_\{[^}]*\}|_.)", body):
        if not piece:
            continue
        if piece.startswith("_"):
            out.append(("sub", piece.strip("_{}")))
        else:
            out.append(("italic", piece))
    return out


def runs(text: str) -> list[tuple[str, str]]:
    """Return (style, text) pieces. Raises on any command this builder does not understand."""
    out = []
    for part in TOKEN.split(text):
        if not part:
            continue
        if part.startswith("$"):
            out.extend(math_runs(part[1:-1]))
            continue
        match = re.match(r"\\text(bf|it|tt)\{(.*)\}$", part)
        if match:
            out.append(({"bf": "bold", "it": "italic", "tt": "mono"}[match.group(1)], match.group(2)))
            continue
        if "\\" in part or "{" in part:
            raise ValueError(f"unhandled LaTeX: {part[:80]}")
        out.append(("", part))
    return out


def table_rows(body: str) -> list[list[str]]:
    start = body.index("\\toprule")
    inner = body[start:body.index("\\bottomrule")].replace("\\toprule", "").replace("\\midrule", "")
    rows = []
    for line in inner.split("\\\\"):
        line = line.strip()
        if line:
            rows.append([cell.strip() for cell in re.split(r"(?<!\\)&", line)])
    return rows


def table_widths(body: str, count: int, total: float) -> list[float]:
    spec = re.search(r"\\begin\{tabular\}\{(.*)\}\s*\n", body).group(1)
    fractions = [float(v) for v in re.findall(r"p\{(\.\d+)\\", spec)]
    if len(fractions) == count:
        scale = total / max(sum(fractions), 1e-9)
        return [f * scale for f in fractions]
    return [total * 0.55] + [total * 0.45 / (count - 1)] * (count - 1)


def bib_items(bbl: str) -> list[str]:
    items = []
    for chunk in re.split(r"\\bibitem\{[^}]+\}", bbl)[1:]:
        chunk = chunk.split("\\end{thebibliography}")[0]
        chunk = re.sub(r"\\BIBentryALTinterwordspacing|\\BIBentrySTDinterwordspacing|\\BIBentryALTinterwordstretchfactor", "", chunk)
        chunk = re.sub(r"\\url\{([^}]*)\}|\\href\{[^}]*\}\{([^}]*)\}", lambda m: m.group(1) or m.group(2), chunk)
        chunk = re.sub(r"\\emph\{([^{}]*)\}|\\textit\{([^{}]*)\}", lambda m: m.group(1) or m.group(2), chunk)
        chunk = chunk.replace("\\newblock", " ").replace("\\BIBforeignlanguage", "")
        chunk = chunk.replace("{\\v{z}}", "ž").replace("\\v{z}", "ž").replace("\\v z", "ž").replace('\\"u', "ü").replace('\\"a', "ä").replace("\\u g", "ğ").replace("\\u{g}", "ğ")
        chunk = re.sub(r"\\relax|\\url|\\Hy@\w+|\\,", "", chunk)
        chunk = chunk.replace("~", "\u00a0").replace("--", "–").replace("``", "“").replace("''", "”").replace("\\&", "&").replace("\\_", "_").replace("\\%", "%")
        chunk = re.sub(r"[{}]", "", chunk)
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if "\\" in chunk:
            raise ValueError(f"unhandled bibliography text: {chunk[:120]}")
        items.append(chunk)
    return items


def render_figure(relative: str) -> Path:
    path = HERE / relative
    if path.suffix != ".pdf":
        return path
    import pymupdf

    target = Path(tempfile.gettempdir()) / f"anima_paper_{path.stem}.png"
    with pymupdf.open(path) as document:
        document[0].get_pixmap(dpi=300).save(target)
    return target


class Writer:
    def __init__(self):
        from docx import Document
        from docx.shared import Inches, Pt

        self.Inches, self.Pt = Inches, Pt
        self.doc = Document()
        style = self.doc.styles["Normal"]
        style.font.name, style.font.size = "Times New Roman", Pt(10)
        style.element.rPr.rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", "Times New Roman")
        style.paragraph_format.space_after = Pt(0)
        section = self.doc.sections[0]
        section.page_width, section.page_height = Inches(8.5), Inches(11)
        section.left_margin = section.right_margin = Inches(0.67)
        section.top_margin, section.bottom_margin = Inches(0.75), Inches(0.9)
        self.columns(section, 1)

    def columns(self, section, count):
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        sect = section._sectPr
        for old in sect.findall(qn("w:cols")):
            sect.remove(old)
        cols = OxmlElement("w:cols")
        cols.set(qn("w:num"), str(count))
        cols.set(qn("w:space"), "230")
        sect.append(cols)

    def layout(self, count):
        from docx.enum.section import WD_SECTION

        self.columns(self.doc.add_section(WD_SECTION.CONTINUOUS), count)

    def para(self, pieces, *, align="justify", size=None, indent=False, before=0, after=0, keep=False, small_caps=False, italic=False, bold=False):
        from docx.enum.text import WD_ALIGN_PARAGRAPH as A

        paragraph = self.doc.add_paragraph()
        fmt = paragraph.paragraph_format
        fmt.alignment = {"justify": A.JUSTIFY, "center": A.CENTER, "left": A.LEFT}[align]
        fmt.space_before, fmt.space_after, fmt.keep_with_next = self.Pt(before), self.Pt(after), keep
        if indent:
            fmt.first_line_indent = self.Inches(0.14)
        self.fill(paragraph, pieces, size, small_caps, italic, bold)
        return paragraph

    def fill(self, paragraph, pieces, size=None, small_caps=False, italic=False, bold=False):
        for kind, text in pieces:
            run = paragraph.add_run(text)
            run.bold = True if kind == "bold" or bold else None
            run.italic = True if kind == "italic" or italic else None
            run.font.subscript = True if kind == "sub" else None
            run.font.small_caps = small_caps or None
            if kind == "mono":
                run.font.name = "Courier New"
            if size:
                run.font.size = self.Pt(size if kind != "mono" else size - 1)

    def caption(self, text, *, table=False):
        kept = table
        self.para([("", text)], align="center" if table else "justify", size=8, before=4 if table else 2, after=2 if table else 8, keep=kept)

    def picture(self, path, width):
        from docx.enum.text import WD_ALIGN_PARAGRAPH as A

        paragraph = self.doc.add_paragraph()
        paragraph.paragraph_format.alignment = A.CENTER
        paragraph.paragraph_format.space_before = self.Pt(6)
        paragraph.paragraph_format.keep_with_next = True
        paragraph.add_run().add_picture(str(path), width=self.Inches(width))

    def table(self, rows, widths, convert):
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        table = self.doc.add_table(rows=len(rows), cols=len(rows[0]))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "bottom"):
            line = OxmlElement(f"w:{edge}")
            line.set(qn("w:val"), "single")
            line.set(qn("w:sz"), "8")
            line.set(qn("w:color"), "000000")
            borders.append(line)
        table._tbl.tblPr.append(borders)
        for i, row in enumerate(rows):
            properties = table.rows[i]._tr.get_or_add_trPr()
            properties.append(OxmlElement("w:cantSplit"))
            if i == 0:
                properties.append(OxmlElement("w:tblHeader"))
            for j, value in enumerate(row):
                cell = table.cell(i, j)
                cell.width = self.Inches(widths[j])
                self.fill(cell.paragraphs[0], convert(value), 8)
                if i == 0:
                    cell_props = cell._tc.get_or_add_tcPr()
                    under = OxmlElement("w:tcBorders")
                    line = OxmlElement("w:bottom")
                    line.set(qn("w:val"), "single")
                    line.set(qn("w:sz"), "4")
                    line.set(qn("w:color"), "000000")
                    under.append(line)
                    cell_props.append(under)
        self.para([], after=6)


def build() -> Path:
    tex = (HERE / "main.tex").read_text(encoding="utf-8")
    tex = re.sub(r"\\input\{([^}]+)\}", lambda m: (HERE / m.group(1)).read_text(encoding="utf-8-sig"), tex)
    bbl = (HERE / "main.bbl").read_text(encoding="utf-8-sig")
    cites, labels = citation_numbers(bbl), label_numbers(tex)
    convert = lambda text: runs(plain(text, cites, labels))
    title = re.search(r"\\title\{([^}]+)\}", tex).group(1)
    authors = re.search(r"\\IEEEauthorblockN\{([^}]+)\}", tex).group(1)
    affiliation = re.search(r"\\IEEEauthorblockA\{([^}]+)\}", tex).group(1).split("\\\\")
    abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", tex, re.S).group(1).strip()
    keywords = re.search(r"\\begin\{IEEEkeywords\}(.*?)\\end\{IEEEkeywords\}", tex, re.S).group(1).strip()
    body = tex.split("\\end{IEEEkeywords}")[1].split("\\bibliographystyle")[0]

    w = Writer()
    w.para([("", title)], align="center", size=22, after=8)
    w.para([("", authors)], align="center", size=11)
    for line in affiliation:
        w.para([("", line.strip())], align="center", size=10, italic=line.strip() == "RV University")
    w.para([], after=8)
    w.layout(2)
    w.para([("bold", "Abstract—"), *[("bold", text) for _, text in convert(abstract)]], size=9, after=4)
    w.para([("bold", "Keywords—"), *[("bold", text) for _, text in convert(keywords)]], size=9, after=4)

    sections = subsections = 0
    pattern = re.compile(r"(\\section\*?\{[^}]*\}|\\begin\{align\}.*?\\end\{align\}|\\subsection\{[^}]*\}|\\begin\{(?:figure|table)\*?\}.*?\\end\{(?:figure|table)\*?\})", re.S)
    for chunk in pattern.split(body):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("\\section*"):
            w.para([("", chunk[10:-1])], align="center", before=8, after=4, keep=True, small_caps=True)
        elif chunk.startswith("\\begin{align}"):
            for line in chunk[len("\\begin{align}"):-len("\\end{align}")].split("\\\\"):
                w.para(math_runs(" ".join(line.replace("\\nonumber", "").replace("&", "").split())), align="center", after=2)
        elif chunk.startswith("\\section"):
            sections, subsections = sections + 1, 0
            w.para([("", f"{ROMAN[sections - 1]}. {chunk[9:-1]}")], align="center", before=8, after=4, keep=True, small_caps=True)
        elif chunk.startswith("\\subsection"):
            subsections += 1
            w.para([("", f"{chr(64 + subsections)}. {chunk[12:-1]}")], align="left", before=6, after=2, keep=True, italic=True)
        elif chunk.startswith("\\begin{figure"):
            wide = chunk.startswith("\\begin{figure*")
            spec = re.search(r"\\includegraphics\[width=(\.\d+)\\(\w+)\]\{([^}]+)\}", chunk)
            caption = plain(re.search(r"\\caption\{(.*?)\}\s*\n\\label", chunk, re.S).group(1), cites, labels)
            number = labels[re.search(r"\\label\{([^}]+)\}", chunk).group(1)]
            if wide:
                w.layout(1)
            base = TEXT_IN if wide else COLUMN_IN
            w.picture(render_figure(spec.group(3)), float(spec.group(1)) * base)
            w.caption(f"Fig. {number}. {caption}")
            if wide:
                w.layout(2)
        elif chunk.startswith("\\begin{table"):
            wide = chunk.startswith("\\begin{table*")
            caption = plain(re.search(r"\\caption\{(.*?)\}\s*\n\\label", chunk, re.S).group(1), cites, labels)
            number = labels[re.search(r"\\label\{([^}]+)\}", chunk).group(1)]
            if wide:
                w.layout(1)
            w.caption(f"TABLE {number}. {caption}", table=True)
            rows = table_rows(chunk)
            w.table(rows, table_widths(chunk, len(rows[0]), (TEXT_IN if wide else COLUMN_IN) * 0.96), convert)
            if wide:
                w.layout(2)
        else:
            for paragraph in re.split(r"\n\s*\n", chunk):
                if paragraph.strip():
                    w.para(convert(" ".join(paragraph.split())), indent=True, after=2)
    w.para([("", "References")], align="center", before=8, after=4, keep=True, small_caps=True)
    for number, item in enumerate(bib_items(bbl), 1):
        paragraph = w.para([("", f"[{number}] {item}")], align="left", size=8, after=2)
        paragraph.paragraph_format.left_indent = w.Inches(0.22)
        paragraph.paragraph_format.first_line_indent = w.Inches(-0.22)
    props = w.doc.core_properties
    props.title, props.author = title, authors
    props.last_modified_by, props.comments = "Mohan Chandra S S", ""
    props.keywords = "NPC, LLM, RAG, computer vision, orchestration"
    w.doc.save(OUTPUT)
    return OUTPUT


if __name__ == "__main__":
    print(build())
