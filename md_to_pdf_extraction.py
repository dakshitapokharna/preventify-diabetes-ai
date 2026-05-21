"""Convert HOW_EXTRACTION_WORKS.md to PDF using fpdf2 with Unicode support."""
from fpdf import FPDF
from fpdf.enums import XPos, YPos
import re

MD_FILE = "HOW_EXTRACTION_WORKS.md"
PDF_FILE = "HOW_EXTRACTION_WORKS.pdf"

FONT_PATH = r"C:\Windows\Fonts"


def find_font(candidates):
    import os
    for name in candidates:
        p = os.path.join(FONT_PATH, name)
        if os.path.exists(p):
            return p
    return None


REGULAR = find_font(["calibri.ttf", "arial.ttf", "segoeui.ttf", "verdana.ttf"])
BOLD    = find_font(["calibrib.ttf", "arialbd.ttf", "segoeuib.ttf", "verdanab.ttf"])
ITALIC  = find_font(["calibrii.ttf", "ariali.ttf", "segoeuii.ttf", "verdanai.ttf"])
MONO    = find_font(["consola.ttf", "cour.ttf", "lucon.ttf"])

print(f"Fonts: regular={REGULAR}, bold={BOLD}, italic={ITALIC}, mono={MONO}")


class PDF(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-12)
        self.set_font("body", style="", size=8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

    def setup_fonts(self):
        if REGULAR:
            self.add_font("body", style="", fname=REGULAR)
        if BOLD:
            self.add_font("body", style="B", fname=BOLD)
        if ITALIC:
            self.add_font("body", style="I", fname=ITALIC)
        if MONO:
            self.add_font("mono", style="", fname=MONO)

    def _font(self, style="", size=10):
        try:
            self.set_font("body", style=style, size=size)
        except Exception:
            self.set_font("Helvetica", style=style, size=size)

    def _mono(self, size=8):
        try:
            self.set_font("mono", size=size)
        except Exception:
            self.set_font("Courier", size=size)

    def chapter_title(self, text, level=1):
        self.ln(3)
        if level == 1:
            self._font("B", 15)
            self.set_fill_color(25, 80, 150)
            self.set_text_color(255, 255, 255)
            self.multi_cell(0, 9, text, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_text_color(0, 0, 0)
        elif level == 2:
            self._font("B", 12)
            self.set_text_color(25, 80, 150)
            self.multi_cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_text_color(0, 0, 0)
            self.set_draw_color(25, 80, 150)
            self.set_line_width(0.4)
            y = self.get_y()
            self.line(self.l_margin, y, self.l_margin + 180, y)
            self.ln(1)
        elif level == 3:
            self._font("B", 10)
            self.set_text_color(50, 50, 50)
            self.multi_cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_text_color(0, 0, 0)
        self.ln(1)

    def body_text(self, text):
        self._font("", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def bullet_item(self, text, indent=8):
        self._font("", 10)
        self.set_text_color(30, 30, 30)
        self.set_x(self.l_margin + indent)
        self.cell(5, 5.5, "•")
        self.set_x(self.l_margin + indent + 5)
        self.multi_cell(0, 5.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def code_block(self, text):
        self._mono(8)
        self.set_fill_color(240, 240, 240)
        self.set_text_color(40, 40, 40)
        self.set_draw_color(200, 200, 200)
        self.multi_cell(0, 4.5, text, fill=True, border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self._font("", 10)
        self.set_text_color(30, 30, 30)
        self.ln(2)

    def blockquote(self, text):
        self._font("I", 10)
        self.set_text_color(70, 70, 70)
        self.set_fill_color(245, 245, 245)
        self.set_x(self.l_margin + 6)
        self.multi_cell(174, 5.5, text, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(30, 30, 30)
        self.ln(1)

    def hr(self):
        self.ln(3)
        self.set_draw_color(200, 200, 200)
        self.set_line_width(0.3)
        y = self.get_y()
        self.line(self.l_margin, y, self.l_margin + 180, y)
        self.ln(4)

    def draw_table(self, rows):
        if not rows:
            return
        n_cols = len(rows[0])
        col_w = 180 // n_cols

        for ri, row in enumerate(rows):
            while len(row) < n_cols:
                row.append("")

            # Calculate row height
            max_lines = 1
            for ci, cell in enumerate(row[:n_cols]):
                cell_str = str(cell)
                w = self.get_string_width(cell_str)
                est_lines = max(1, int(w / (col_w - 3)) + 1)
                max_lines = max(max_lines, est_lines)
            row_h = max_lines * 5 + 3

            if ri == 0:
                self._font("B", 9)
                self.set_fill_color(25, 80, 150)
                self.set_text_color(255, 255, 255)
            else:
                self._font("", 9)
                fill = ri % 2 == 0
                self.set_fill_color(248, 248, 248) if fill else self.set_fill_color(255, 255, 255)
                self.set_text_color(30, 30, 30)

            y_start = self.get_y()
            x_start = self.l_margin

            for ci, cell in enumerate(row[:n_cols]):
                self.set_xy(x_start + ci * col_w, y_start)
                self.multi_cell(col_w, 5, str(cell), border=1,
                                fill=(ri == 0 or ri % 2 == 0),
                                new_x=XPos.RIGHT, new_y=YPos.TOP)

            self.set_y(y_start + row_h)

        self.set_text_color(30, 30, 30)
        self.ln(3)


def strip_inline_md(text):
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


def parse_table(table_lines):
    rows = []
    for line in table_lines:
        line = line.strip()
        if re.match(r"^\|[-| :]+\|$", line):
            continue
        cells = [strip_inline_md(c.strip()) for c in line.strip("|").split("|")]
        rows.append(cells)
    return rows


with open(MD_FILE, encoding="utf-8") as f:
    lines = f.readlines()

pdf = PDF()
pdf.set_margins(15, 15, 15)
pdf.setup_fonts()
pdf.add_page()
pdf.set_auto_page_break(auto=True, margin=18)

i = 0
in_code = False
code_buf = []
table_buf = []

while i < len(lines):
    raw = lines[i].rstrip("\n")
    stripped = raw.strip()

    # Code block toggle
    if stripped.startswith("```"):
        if not in_code:
            in_code = True
            code_buf = []
        else:
            in_code = False
            pdf.code_block("\n".join(code_buf))
        i += 1
        continue
    if in_code:
        code_buf.append(raw)
        i += 1
        continue

    # Table accumulation
    if stripped.startswith("|"):
        table_buf.append(stripped)
        i += 1
        continue
    elif table_buf:
        rows = parse_table(table_buf)
        pdf.draw_table(rows)
        table_buf = []

    # Heading
    m = re.match(r"^(#{1,3})\s+(.*)", stripped)
    if m:
        level = len(m.group(1))
        text = strip_inline_md(m.group(2))
        pdf.chapter_title(text, level)
        i += 1
        continue

    # HR
    if stripped in ("---", "***", "___"):
        pdf.hr()
        i += 1
        continue

    # Blockquote
    if stripped.startswith(">"):
        text = strip_inline_md(stripped.lstrip("> ").lstrip(">"))
        pdf.blockquote(text)
        i += 1
        continue

    # Bullet
    m = re.match(r"^[-*]\s+(.*)", stripped)
    if m:
        pdf.bullet_item(strip_inline_md(m.group(1)))
        i += 1
        continue

    # Empty
    if stripped == "":
        pdf.ln(2)
        i += 1
        continue

    # Normal paragraph
    pdf.body_text(strip_inline_md(stripped))
    i += 1

# Flush trailing table
if table_buf:
    rows = parse_table(table_buf)
    pdf.draw_table(rows)

pdf.output(PDF_FILE)
print(f"Done -> {PDF_FILE}")
