"""
Convert BOT_CONVERSATION_ARCHITECTURE.md to PDF using fpdf2.
Run: python tools/md_to_pdf.py
"""

import re
from pathlib import Path
from fpdf import FPDF
from fpdf.enums import XPos, YPos

INPUT_MD  = Path(__file__).parent.parent / "BOT_CONVERSATION_ARCHITECTURE.md"
OUTPUT_PDF = Path(__file__).parent.parent / "BOT_CONVERSATION_ARCHITECTURE.pdf"

# Unicode → latin-1 safe substitution table
_UNICODE_SUBS = str.maketrans({
    "—": "--",   # em dash
    "–": "-",    # en dash
    "’": "'",    # right single quote
    "‘": "'",    # left single quote
    "“": '"',    # left double quote
    "”": '"',    # right double quote
    "…": "...",  # ellipsis
    " ": " ",    # non-breaking space
    "→": "->",   # right arrow
    "←": "<-",   # left arrow
    "•": "*",    # bullet
    "►": ">",    # filled right arrow
    "✓": "v",    # check mark
    "✗": "x",    # cross mark
    "×": "x",    # multiplication sign
    "±": "+/-",  # plus-minus
    "≤": "<=",   # less than or equal
    "≥": ">=",   # greater than or equal
    "α": "alpha",
    "β": "beta",
})

def _safe(text: str) -> str:
    """Replace non-latin-1 characters with safe ASCII equivalents."""
    text = text.translate(_UNICODE_SUBS)
    return text.encode("latin-1", errors="replace").decode("latin-1")

C_BLACK   = (30,  30,  30)
C_HEADING = (20,  60, 100)
C_H3      = (40,  90, 140)
C_CODE_BG = (240, 243, 246)
C_CODE_FG = (30,  30,  30)
C_TABLE_H = (20,  60, 100)
C_TABLE_R = (248, 250, 252)
C_LINE    = (180, 190, 200)
C_WHITE   = (255, 255, 255)


class MarkdownPDF(FPDF):

    def header(self):
        if self.page_no() <= 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*C_LINE)
        self.cell(0, 8, _safe("Preventify Bot -- Conversation & API Architecture"), align="L")
        self.cell(0, 8, f"Page {self.page_no()}", align="R",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*C_LINE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*C_LINE)
        self.cell(0, 6,
                  _safe("Preventify -- Internal Engineering Reference -- v1.0 -- 2026-05-23"),
                  align="C")

    def _add_heading(self, text, level):
        text = _safe(text)
        self.ln(4 if level > 1 else 8)
        if level == 1:
            self.set_font("Helvetica", "B", 18)
            self.set_text_color(*C_HEADING)
            self.multi_cell(0, 10, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_draw_color(*C_HEADING)
            self.set_line_width(0.6)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.ln(3)
        elif level == 2:
            self.set_font("Helvetica", "B", 13)
            self.set_text_color(*C_HEADING)
            self.multi_cell(0, 8, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_draw_color(*C_LINE)
            self.set_line_width(0.3)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.ln(2)
        elif level == 3:
            self.set_font("Helvetica", "B", 11)
            self.set_text_color(*C_H3)
            self.multi_cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(1)
        else:
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*C_BLACK)
            self.multi_cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_line_width(0.2)
        self.set_text_color(*C_BLACK)

    def _add_paragraph(self, text):
        text = text.strip()
        if not text:
            return
        # strip inline code and links to plain text
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        # handle bold inline
        parts = re.split(r'(\*\*[^*]+\*\*)', text)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*C_BLACK)
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                self.set_font("Helvetica", "B", 10)
                self.write(6, _safe(part[2:-2]))
            else:
                self.set_font("Helvetica", "", 10)
                self.write(6, _safe(part))
        self.ln(6)
        self.ln(1)

    def _add_bullet(self, text, indent=0):
        text = text.strip()
        if not text:
            return
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        bullet_x = self.l_margin + indent * 5
        text_x   = bullet_x + 5
        self.set_x(bullet_x)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*C_BLACK)
        self.cell(5, 5, chr(149), new_x=XPos.RIGHT, new_y=YPos.TOP)
        avail = self.w - self.r_margin - text_x
        self.set_x(text_x)
        self.multi_cell(avail, 5, _safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def _add_code_block(self, lines):
        self.ln(2)
        self.set_fill_color(*C_CODE_BG)
        self.set_text_color(*C_CODE_FG)
        self.set_font("Courier", "", 7.5)
        pad = 4
        content = _safe("\n".join(lines))
        line_count = len(lines)
        block_h = line_count * 4.2 + pad * 2
        x = self.l_margin
        w = self.w - self.l_margin - self.r_margin
        if self.get_y() + block_h > self.h - self.b_margin - 10:
            self.add_page()
        y_start = self.get_y()
        self.rect(x, y_start, w, block_h, style="F")
        self.set_xy(x + pad, y_start + pad)
        self.multi_cell(w - pad * 2, 4.2, content,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)
        self.set_text_color(*C_BLACK)

    def _add_table(self, headers, rows):
        self.ln(2)
        col_count = len(headers)
        if col_count == 0:
            return
        avail = self.w - self.l_margin - self.r_margin
        col_w = avail / col_count

        self.set_fill_color(*C_TABLE_H)
        self.set_text_color(*C_WHITE)
        self.set_font("Helvetica", "B", 8)
        for h in headers:
            self.cell(col_w, 6, _safe(h[:45]), border=0, fill=True,
                      new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.ln(6)

        self.set_font("Helvetica", "", 8)
        for idx, row in enumerate(rows):
            fill = (idx % 2 == 0)
            self.set_fill_color(*(C_TABLE_R if fill else C_WHITE))
            self.set_text_color(*C_BLACK)
            for j in range(col_count):
                val = row[j] if j < len(row) else ""
                val = re.sub(r'\*\*([^*]+)\*\*', r'\1', val)
                val = re.sub(r'`([^`]+)`', r'\1', val)
                val = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', val)
                self.cell(col_w, 5.5, _safe(val[:55]), border=0, fill=fill,
                          new_x=XPos.RIGHT, new_y=YPos.TOP)
            self.ln(5.5)

        self.set_draw_color(*C_LINE)
        self.line(self.l_margin, self.get_y(),
                  self.w - self.r_margin, self.get_y())
        self.ln(3)


def is_table_row(line):
    s = line.strip()
    return s.startswith("|") and s.endswith("|")

def is_separator_row(line):
    return bool(re.match(r'^\s*\|[-| :]+\|\s*$', line))

def parse_table_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_and_render(pdf: MarkdownPDF, md_text: str):
    lines = md_text.splitlines()
    i = 0
    in_code = False
    code_buf = []
    table_headers = []
    table_buf = []

    def flush_table():
        nonlocal table_headers, table_buf
        if table_headers:
            pdf._add_table(table_headers, table_buf)
        table_headers.clear()
        table_buf.clear()

    while i < len(lines):
        line = lines[i]

        # fenced code
        if line.strip().startswith("```"):
            if in_code:
                pdf._add_code_block(code_buf)
                code_buf.clear()
                in_code = False
            else:
                flush_table()
                in_code = True
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # table
        if is_table_row(line):
            if is_separator_row(line):
                i += 1
                continue
            row = parse_table_row(line)
            if not table_headers:
                table_headers = row
            else:
                table_buf.append(row)
            i += 1
            continue
        else:
            flush_table()

        stripped = line.strip()

        if not stripped:
            pdf.ln(2)
            i += 1
            continue

        # horizontal rule
        if re.match(r'^[-_]{3,}$', stripped):
            pdf.set_draw_color(*C_LINE)
            pdf.line(pdf.l_margin, pdf.get_y(),
                     pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)
            i += 1
            continue

        # headings
        m = re.match(r'^(#{1,4})\s+(.*)', stripped)
        if m:
            level = len(m.group(1))
            text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', m.group(2))
            text = re.sub(r'`([^`]+)`', r'\1', text)
            pdf._add_heading(text, level)
            i += 1
            continue

        # bullets
        m = re.match(r'^(\s*)[-*]\s+(.*)', line)
        if m:
            indent = len(m.group(1)) // 2
            text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', m.group(2))
            text = re.sub(r'`([^`]+)`', r'\1', text)
            pdf._add_bullet(text, indent)
            i += 1
            continue

        # numbered list
        m = re.match(r'^\s*\d+\.\s+(.*)', stripped)
        if m:
            text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', m.group(1))
            text = re.sub(r'`([^`]+)`', r'\1', text)
            pdf._add_bullet(text)
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            text = stripped.lstrip("> ").strip()
            text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
            text = re.sub(r'`([^`]+)`', r'\1', text)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(100, 100, 100)
            pdf.set_fill_color(245, 245, 245)
            pdf.multi_cell(0, 5, _safe(text), fill=True,
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(*C_BLACK)
            pdf.ln(1)
            i += 1
            continue

        # paragraph
        pdf._add_paragraph(stripped)
        i += 1

    flush_table()


def build_pdf():
    md_text = INPUT_MD.read_text(encoding="utf-8")
    pdf = MarkdownPDF()
    pdf.set_margins(left=18, top=20, right=18)
    pdf.set_auto_page_break(auto=True, margin=18)

    # cover page
    pdf.add_page()
    pdf.set_fill_color(20, 60, 100)
    pdf.rect(0, 0, pdf.w, 75, style="F")
    pdf.set_y(18)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(255, 255, 255)
    pdf.multi_cell(0, 12, "Preventify Bot", align="C",
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 13)
    pdf.multi_cell(0, 8, "Conversation & API Architecture", align="C",
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_y(85)
    pdf.set_text_color(*C_BLACK)
    for label, val in [
        ("Version",  "1.0"),
        ("Date",     "2026-05-23"),
        ("Status",   "Approved for Engineering Build"),
        ("Project",  "Preventify Diabetes Educator AI -- Kerala"),
    ]:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(50, 7, _safe(label + ":"), new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 7, _safe(val), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(8)
    pdf.set_draw_color(*C_LINE)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5,
        "Internal engineering reference. "
        "Do not share outside the Preventify engineering and clinical team.",
        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # content
    pdf.add_page()
    parse_and_render(pdf, md_text)
    pdf.output(str(OUTPUT_PDF))
    print(f"PDF written: {OUTPUT_PDF}")


if __name__ == "__main__":
    build_pdf()
