"""
md_to_pdf.py — Convert Markdown files to PDF using fpdf2.
Handles: headings (H1–H3), bold, inline code, fenced code blocks,
         bullet lists, numbered lists, tables, horizontal rules, blockquotes.

Usage:
    python tools/md_to_pdf.py CHUNKING_LOGIC.md
    python tools/md_to_pdf.py CHUNKING_DISCUSSION.md
    python tools/md_to_pdf.py CHUNKING_LOGIC.md CHUNKING_DISCUSSION.md
"""

import re
import sys
from pathlib import Path
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ── Colours ────────────────────────────────────────────────────────────────
C_H1        = (15,  78, 137)   # dark blue
C_H2        = (30, 100, 160)
C_H3        = (50, 120, 180)
C_CODE_BG   = (240, 243, 246)  # light grey background for code blocks
C_TABLE_HD  = (220, 230, 242)  # table header fill
C_TABLE_ALT = (248, 250, 252)  # alternating row fill
C_RULE      = (180, 190, 200)
C_QUOTE_BAR = (100, 140, 190)
C_BODY      = (30,  30,  30)

# ── Layout constants ────────────────────────────────────────────────────────
MARGIN      = 18   # mm
LINE_H      = 5.5  # mm  normal body line height
CODE_LINE_H = 4.8  # mm  code block line height
TABLE_ROW_H = 6    # mm

# ── Segment helpers ─────────────────────────────────────────────────────────

def strip_inline(text: str) -> str:
    """Remove all inline markup, return plain text."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`",      r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def segments(text: str):
    """
    Yield (style, content) segments from an inline-marked-up string.
    style ∈ {"normal", "bold", "code"}
    """
    pattern = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")
    cursor = 0
    for m in pattern.finditer(text):
        if m.start() > cursor:
            yield ("normal", text[cursor:m.start()])
        if m.group(1) is not None:
            yield ("bold",   m.group(1))
        else:
            yield ("code",   m.group(2))
        cursor = m.end()
    if cursor < len(text):
        yield ("normal", text[cursor:])


# ── PDF class ───────────────────────────────────────────────────────────────

FONTS_DIR = Path("C:/Windows/Fonts")

class MdPDF(FPDF):

    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=True, margin=16)

        # Load Unicode TTF fonts (always present on Windows)
        self.add_font("Arial",    style="",   fname=str(FONTS_DIR / "arial.ttf"))
        self.add_font("Arial",    style="B",  fname=str(FONTS_DIR / "arialbd.ttf"))
        self.add_font("Arial",    style="I",  fname=str(FONTS_DIR / "ariali.ttf"))
        self.add_font("Arial",    style="BI", fname=str(FONTS_DIR / "arialbi.ttf"))
        self.add_font("CourierN", style="",   fname=str(FONTS_DIR / "cour.ttf"))
        self.add_font("CourierN", style="B",  fname=str(FONTS_DIR / "courbd.ttf"))

        self.add_page()
        self._body_font   = "Arial"
        self._mono_font   = "CourierN"
        self._eff_w       = self.w - 2 * MARGIN   # effective text width

    # ── low-level helpers ───────────────────────────────────────────────────

    def _set_body(self, size=10, style=""):
        self.set_font(self._body_font, style, size)
        self.set_text_color(*C_BODY)

    def _set_mono(self, size=8.5, style=""):
        self.set_font(self._mono_font, style, size)
        self.set_text_color(*C_BODY)

    def _vspace(self, mm=2):
        self.ln(mm)

    # ── block renderers ─────────────────────────────────────────────────────

    def render_h1(self, text: str):
        self._vspace(4)
        self.set_font(self._body_font, "B", 16)
        self.set_text_color(*C_H1)
        self.multi_cell(self._eff_w, 9, strip_inline(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        # underline rule
        self.set_draw_color(*C_H1)
        self.set_line_width(0.6)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.set_line_width(0.2)
        self._vspace(3)

    def render_h2(self, text: str):
        self._vspace(4)
        self.set_font(self._body_font, "B", 13)
        self.set_text_color(*C_H2)
        self.multi_cell(self._eff_w, 7.5, strip_inline(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*C_H2)
        self.set_line_width(0.3)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.set_line_width(0.2)
        self._vspace(2)

    def render_h3(self, text: str):
        self._vspace(3)
        self.set_font(self._body_font, "B", 11)
        self.set_text_color(*C_H3)
        self.multi_cell(self._eff_w, 6.5, strip_inline(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self._vspace(1.5)

    def render_h4(self, text: str):
        self._vspace(2)
        self.set_font(self._body_font, "BI", 10)
        self.set_text_color(*C_H3)
        self.multi_cell(self._eff_w, LINE_H, strip_inline(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self._vspace(1)

    def render_rule(self):
        self._vspace(2)
        self.set_draw_color(*C_RULE)
        self.set_line_width(0.4)
        self.line(MARGIN, self.get_y(), self.w - MARGIN, self.get_y())
        self.set_line_width(0.2)
        self._vspace(2)

    def render_paragraph(self, text: str):
        """Render a paragraph with inline bold/code segments."""
        self._set_body()
        # We'll write segment-by-segment using write()
        for style, content in segments(text):
            if style == "bold":
                self.set_font(self._body_font, "B", 10)
            elif style == "code":
                self._set_mono(9)
                self.set_text_color(140, 40, 40)
            else:
                self._set_body()
            self.write(LINE_H, content)
        self.ln(LINE_H)
        self._vspace(1)

    def render_bullet(self, text: str, level: int = 0, ordered_n: int | None = None):
        indent = MARGIN + 5 + level * 5
        bullet_w = 8
        text_w   = self._eff_w - (indent - MARGIN) - bullet_w

        self._set_body()
        # bullet character
        bullet = f"{ordered_n}." if ordered_n is not None else "•"
        self.set_x(indent)
        self.cell(bullet_w, LINE_H, bullet)

        # text with inline markup — write() starting from current x
        for style, content in segments(strip_inline(text) if False else text):
            # actually render with markup
            if style == "bold":
                self.set_font(self._body_font, "B", 10)
            elif style == "code":
                self._set_mono(9)
                self.set_text_color(140, 40, 40)
            else:
                self._set_body()
            # Use multi_cell for wrapping, but only for the last segment? tricky.
            # Simpler: collect all plain text, render at once.

        # Simpler approach: strip inline markup and render plain
        self._set_body()
        self.set_x(indent + bullet_w)
        self.multi_cell(text_w, LINE_H, strip_inline(text),
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def render_code_block(self, lines: list[str]):
        self._vspace(2)
        # background rect — height estimated
        line_count = len(lines)
        block_h    = line_count * CODE_LINE_H + 4
        x0, y0     = MARGIN, self.get_y()

        # check page break
        if y0 + block_h > self.h - self.b_margin:
            self.add_page()
            y0 = self.get_y()

        self.set_fill_color(*C_CODE_BG)
        self.rect(x0, y0, self._eff_w, block_h, style="F")

        self._set_mono(8.5)
        self.set_y(y0 + 2)
        for line in lines:
            self.set_x(MARGIN + 3)
            self.cell(self._eff_w - 6, CODE_LINE_H, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self._vspace(3)

    def render_table(self, header: list[str], rows: list[list[str]]):
        self._vspace(2)
        n_cols = len(header)
        if n_cols == 0:
            return

        # Compute column widths proportionally based on max content length
        col_lens = [max(len(h), max((len(r[i]) if i < len(r) else 0) for r in rows) if rows else 0)
                    for i, h in enumerate(header)]
        total_len = sum(col_lens) or 1
        col_widths = [max(18, self._eff_w * cl / total_len) for cl in col_lens]
        # normalise so sum == eff_w
        scale = self._eff_w / sum(col_widths)
        col_widths = [w * scale for w in col_widths]

        def draw_row(cells, fill_color, is_header=False):
            # Clamp to n_cols — data rows may have fewer/more cells than header
            style = "B" if is_header else ""
            self.set_font(self._body_font, style, 9)
            cell_lines = []
            for i in range(n_cols):
                text = strip_inline(cells[i] if i < len(cells) else "")
                char_w = self.get_string_width("W")
                chars_per_line = max(1, int(col_widths[i] / char_w)) if char_w else 20
                n_lines = max(1, (len(text) + chars_per_line - 1) // chars_per_line)
                cell_lines.append(n_lines)
            row_h = max(TABLE_ROW_H, max(cell_lines) * TABLE_ROW_H)

            # page break check
            if self.get_y() + row_h > self.h - self.b_margin:
                self.add_page()

            self.set_fill_color(*fill_color)
            self.set_draw_color(190, 200, 210)
            x_start = self.get_x()
            y_start = self.get_y()

            for i in range(n_cols):
                text = strip_inline(cells[i] if i < len(cells) else "")
                self.set_xy(x_start + sum(col_widths[:i]), y_start)
                self.set_font(self._body_font, style, 9)
                self.set_text_color(*C_BODY)
                self.multi_cell(col_widths[i], row_h, text,
                                border=1, fill=True,
                                new_x=XPos.RIGHT, new_y=YPos.TOP)
            self.set_y(y_start + row_h)

        draw_row(header, C_TABLE_HD, is_header=True)
        for idx, row in enumerate(rows):
            fill = C_TABLE_ALT if idx % 2 == 0 else (255, 255, 255)
            draw_row(row, fill)

        self._vspace(3)

    def render_blockquote(self, text: str):
        self._vspace(1)
        x0, y0 = MARGIN, self.get_y()
        self.set_fill_color(*C_CODE_BG)
        self.set_draw_color(*C_QUOTE_BAR)
        self.set_line_width(1.2)
        self.set_font(self._body_font, "I", 9.5)
        self.set_text_color(70, 70, 70)
        self.set_x(MARGIN + 5)
        self.multi_cell(self._eff_w - 5, LINE_H, strip_inline(text),
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        y1 = self.get_y()
        self.line(MARGIN + 1, y0, MARGIN + 1, y1)
        self.set_line_width(0.2)
        self._vspace(1)

    def header(self):
        pass   # no running header

    def footer(self):
        self.set_y(-12)
        self.set_font(self._body_font, "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


# ── Markdown parser / dispatcher ─────────────────────────────────────────────

def parse_table_row(line: str) -> list[str]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def is_separator_row(cells: list[str]) -> bool:
    return all(re.match(r"^[-:]+$", c) for c in cells if c)


def convert(md_path: Path, pdf_path: Path):
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    pdf = MdPDF()

    i = 0
    n = len(lines)

    # table state
    in_table     = False
    table_header: list[str] = []
    table_rows:   list[list[str]] = []

    # code block state
    in_code  = False
    code_buf: list[str] = []

    # bullet / list state
    list_buf: list[tuple[int, str | None, str]] = []  # (level, order_n, text)

    def flush_list():
        nonlocal list_buf
        for level, order_n, txt in list_buf:
            pdf.render_bullet(txt, level=level, ordered_n=order_n)
        list_buf = []
        pdf._vspace(1)

    def flush_table():
        nonlocal in_table, table_header, table_rows
        if table_header:
            pdf.render_table(table_header, table_rows)
        in_table     = False
        table_header = []
        table_rows   = []

    while i < n:
        raw   = lines[i]
        strip = raw.strip()

        # ── fenced code block ──────────────────────────────────────────────
        if strip.startswith("```"):
            if in_table:  flush_table()
            if list_buf:  flush_list()
            if not in_code:
                in_code  = True
                code_buf = []
                i += 1
                continue
            else:
                pdf.render_code_block(code_buf)
                in_code  = False
                code_buf = []
                i += 1
                continue

        if in_code:
            code_buf.append(raw)
            i += 1
            continue

        # ── horizontal rule ────────────────────────────────────────────────
        if re.match(r"^[-*_]{3,}\s*$", strip):
            if in_table:  flush_table()
            if list_buf:  flush_list()
            pdf.render_rule()
            i += 1
            continue

        # ── headings ───────────────────────────────────────────────────────
        if strip.startswith("#"):
            if in_table:  flush_table()
            if list_buf:  flush_list()
            m = re.match(r"^(#{1,4})\s+(.*)", strip)
            if m:
                level = len(m.group(1))
                title = m.group(2)
                if   level == 1: pdf.render_h1(title)
                elif level == 2: pdf.render_h2(title)
                elif level == 3: pdf.render_h3(title)
                else:            pdf.render_h4(title)
            i += 1
            continue

        # ── table rows ─────────────────────────────────────────────────────
        if strip.startswith("|"):
            if list_buf: flush_list()
            cells = parse_table_row(strip)
            if not in_table:
                # peek next line for separator
                if i + 1 < n and parse_table_row(lines[i + 1].strip()):
                    if is_separator_row(parse_table_row(lines[i + 1].strip())):
                        table_header = cells
                        in_table     = True
                        i += 2   # skip separator
                        continue
                # lone pipe line — treat as paragraph
            else:
                if not is_separator_row(cells):
                    table_rows.append(cells)
                i += 1
                continue

        elif in_table:
            flush_table()

        # ── bullet list ────────────────────────────────────────────────────
        bullet_m = re.match(r"^(\s*)[-*+]\s+(.*)", raw)
        ordered_m = re.match(r"^(\s*)(\d+)\.\s+(.*)", raw)

        if bullet_m:
            level = len(bullet_m.group(1)) // 2
            list_buf.append((level, None, bullet_m.group(2)))
            i += 1
            continue

        if ordered_m:
            level = len(ordered_m.group(1)) // 2
            list_buf.append((level, int(ordered_m.group(2)), ordered_m.group(3)))
            i += 1
            continue

        if list_buf and strip == "":
            flush_list()

        # ── blockquote ─────────────────────────────────────────────────────
        if strip.startswith("> "):
            if list_buf: flush_list()
            pdf.render_blockquote(strip[2:])
            i += 1
            continue

        # ── blank line ─────────────────────────────────────────────────────
        if strip == "":
            if list_buf: flush_list()
            pdf._vspace(1.5)
            i += 1
            continue

        # ── paragraph / normal line ────────────────────────────────────────
        if list_buf: flush_list()
        pdf.render_paragraph(strip)
        i += 1

    # flush anything remaining
    if in_code  and code_buf: pdf.render_code_block(code_buf)
    if in_table:              flush_table()
    if list_buf:              flush_list()

    pdf.output(str(pdf_path))
    print(f"  ✓  {pdf_path.name}  ({pdf.page_no()} pages)")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    root = Path(__file__).parent.parent   # repo root

    targets = sys.argv[1:] if len(sys.argv) > 1 else [
        "CHUNKING_LOGIC.md",
        "CHUNKING_DISCUSSION.md",
    ]

    for name in targets:
        md  = root / name
        pdf = md.with_suffix(".pdf")
        if not md.exists():
            print(f"  SKIP  {name} not found")
            continue
        print(f"Converting {md.name} ...")
        try:
            convert(md, pdf)
        except Exception as e:
            print(f"  ERROR: {e}")
            raise
