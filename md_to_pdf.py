import re
from fpdf import FPDF

MD_FILE = "PROJECT_SUMMARY.md"
PDF_FILE = "PROJECT_SUMMARY.pdf"

FONTS = r"C:\Windows\Fonts"

class PDF(FPDF):
    def __init__(self):
        super().__init__()
        self.add_font("Arial", "",  f"{FONTS}/arial.ttf")
        self.add_font("Arial", "B", f"{FONTS}/arialbd.ttf")
        self.add_font("Arial", "I", f"{FONTS}/ariali.ttf")
        self.add_font("Mono",  "",  f"{FONTS}/consola.ttf")
        self.set_margins(20, 20, 20)
        self.set_auto_page_break(auto=True, margin=20)
        self.add_page()

    def footer(self):
        self.set_y(-12)
        self.set_font("Arial", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, str(self.page_no()), align="C")


def strip_inline(text):
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*',     r'\1', text)
    text = re.sub(r'`(.+?)`',       r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'~~(.+?)~~',     r'\1', text)
    return text


def convert(md_text):
    pdf = PDF()
    lines = md_text.splitlines()
    i = 0
    in_code = False
    code_buf = []
    table_buf = []
    ol_n = 0

    while i < len(lines):
        raw = lines[i]

        # code fence
        if raw.strip().startswith("```"):
            if not in_code:
                in_code = True
                code_buf = []
            else:
                in_code = False
                pdf.set_font("Mono", "", 8.5)
                pdf.set_text_color(30, 30, 30)
                pdf.set_fill_color(245, 245, 245)
                for cl in code_buf:
                    pdf.set_x(20)
                    pdf.cell(0, 5, cl[:110], fill=True, new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
            i += 1
            continue

        if in_code:
            code_buf.append(raw)
            i += 1
            continue

        # table row
        if raw.strip().startswith("|"):
            cells = [c.strip() for c in raw.strip().split("|") if c.strip()]
            if all(re.fullmatch(r'[-: ]+', c) for c in cells):
                i += 1
                continue
            table_buf.append(cells)
            i += 1
            continue
        else:
            if table_buf:
                render_table(pdf, table_buf)
                table_buf = []

        s = raw.strip()

        if s.startswith("# "):
            pdf.ln(3)
            pdf.set_font("Arial", "B", 15)
            pdf.set_text_color(0, 0, 0)
            pdf.multi_cell(0, 8, strip_inline(s[2:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            ol_n = 0

        elif s.startswith("## "):
            pdf.ln(4)
            pdf.set_font("Arial", "B", 12)
            pdf.set_text_color(0, 0, 0)
            pdf.multi_cell(0, 7, strip_inline(s[3:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
            ol_n = 0

        elif s.startswith("### "):
            pdf.ln(3)
            pdf.set_font("Arial", "B", 10.5)
            pdf.set_text_color(0, 0, 0)
            pdf.multi_cell(0, 6, strip_inline(s[4:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
            ol_n = 0

        elif re.fullmatch(r'-{3,}', s):
            pdf.ln(2)
            pdf.set_draw_color(180, 180, 180)
            pdf.line(20, pdf.get_y(), pdf.w - 20, pdf.get_y())
            pdf.ln(2)

        elif s.startswith("- "):
            ol_n = 0
            indent = (len(raw) - len(raw.lstrip())) // 2
            pdf.set_font("Arial", "", 9.5)
            pdf.set_text_color(30, 30, 30)
            pdf.set_x(20 + indent * 4)
            pdf.cell(4, 5.5, "-")
            pdf.set_x(24 + indent * 4)
            pdf.multi_cell(pdf.w - 44 - indent * 4, 5.5, strip_inline(s[2:]),
                           new_x="LMARGIN", new_y="NEXT")

        elif re.match(r'^\d+\. ', s):
            m = re.match(r'^(\d+)\. (.+)', s)
            if m:
                ol_n += 1
                pdf.set_font("Arial", "", 9.5)
                pdf.set_text_color(30, 30, 30)
                pdf.set_x(20)
                pdf.cell(6, 5.5, f"{ol_n}.")
                pdf.set_x(26)
                pdf.multi_cell(pdf.w - 46, 5.5, strip_inline(m.group(2)),
                               new_x="LMARGIN", new_y="NEXT")

        elif s.startswith("> "):
            pdf.set_font("Arial", "I", 9)
            pdf.set_text_color(80, 80, 80)
            pdf.set_x(24)
            pdf.multi_cell(pdf.w - 44, 5.5, strip_inline(s[2:]),
                           new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(30, 30, 30)

        elif s == "":
            pdf.ln(2)
            ol_n = 0

        else:
            ol_n = 0
            pdf.set_font("Arial", "", 9.5)
            pdf.set_text_color(30, 30, 30)
            pdf.multi_cell(0, 5.5, strip_inline(s), new_x="LMARGIN", new_y="NEXT")

        i += 1

    if table_buf:
        render_table(pdf, table_buf)

    pdf.output(PDF_FILE)
    print(f"Written: {PDF_FILE}")


def render_table(pdf, rows):
    if not rows:
        return
    pdf.ln(2)
    n = len(rows[0])
    w = (pdf.w - 40) / n

    for ri, row in enumerate(rows):
        row = (row + [""] * n)[:n]
        bold = ri == 0
        pdf.set_font("Arial", "B" if bold else "", 8.5)
        pdf.set_text_color(0, 0, 0)
        pdf.set_fill_color(230, 230, 230) if bold else pdf.set_fill_color(255, 255, 255)

        # measure row height
        max_lines = 1
        for cell in row:
            lines = pdf.multi_cell(w - 4, 5, strip_inline(cell),
                                   dry_run=True, output="LINES")
            max_lines = max(max_lines, len(lines))
        rh = max_lines * 5 + 4

        if pdf.get_y() + rh > pdf.h - pdf.b_margin:
            pdf.add_page()

        y0 = pdf.get_y()
        for ci, cell in enumerate(row):
            pdf.set_xy(20 + ci * w, y0)
            pdf.multi_cell(w, rh / max(max_lines, 1),
                           strip_inline(cell), border=1, fill=True,
                           new_x="RIGHT", new_y="TOP", max_line_height=5)
        pdf.set_y(y0 + rh)
    pdf.ln(3)


if __name__ == "__main__":
    with open(MD_FILE, encoding="utf-8") as f:
        text = f.read()
    convert(text)
