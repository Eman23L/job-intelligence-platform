"""Turn plain CV text into a clean one- or two-page A4 PDF.

The text is the CV as stored in the app:

    Name
    Headline
    Contact lines...

    SECTION HEADING
    Job title, Company (2021 to present)
    - Bullet point
    Project: Name (stack)
    Paragraph...

Section headings are lines in capitals. Bullets start with "-", "*" or "•". "Target roles" is left out:
it's there for matching jobs, not for employers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fpdf import FPDF

ACCENT = (31, 58, 95)
MUTED = (85, 85, 85)
TEXT = (20, 20, 20)
FONT = "Helvetica"
LEFT_MARGIN = 16
TOP_MARGIN = 14
SECTIONS_LEFT_OUT = {"TARGET ROLES", "PREFERRED ROLES", "ROLES OF INTEREST", "DESIRED ROLES", "PREFERENCES", "JOB PREFERENCES"}
BULLET = re.compile(r"^\s*(?:[-*•‣▪]|–)\s+")
DATES_AT_END = re.compile(r"^(?P<title>.+?)\s*\((?P<dates>[^()]*\d{4}[^()]*)\)\s*$")
PROJECT = re.compile(r"^Project:\s*(?P<name>.+?)(?:\s*\((?P<stack>[^()]+)\))?\s*$", re.IGNORECASE)

# The built-in PDF fonts only cover Latin-1, so swap common typographic characters for plain ones.
_REPLACEMENTS = {
    "–": "-",
    "—": "-",
    "‒": "-",
    "−": "-",
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "…": "...",
    "•": "-",
    " ": " ",
    "→": "->",
    "←": "<-",
    "≤": "<=",
    "≥": ">=",
    "™": "(TM)",
}


@dataclass
class Section:
    heading: str
    lines: list[str] = field(default_factory=list)


@dataclass
class ParsedCv:
    name: str
    headline: str | None
    contact: list[str]
    sections: list[Section]


def latin1(value: str) -> str:
    for old, new in _REPLACEMENTS.items():
        value = value.replace(old, new)
    return value.encode("latin-1", "replace").decode("latin-1")


def is_heading(line: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", line)
    return 2 < len(letters) and len(line) <= 48 and letters.isupper() and "," not in line and not BULLET.match(line)


def parse_cv(cv_text: str) -> ParsedCv:
    lines = [" ".join(line.split()) for line in cv_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    if not lines:
        raise ValueError("The CV is empty.")

    name = lines.pop(0)
    header: list[str] = []
    while lines and lines[0] and not is_heading(lines[0]):
        header.append(lines.pop(0))
    headline = header.pop(0) if header and not _looks_like_contact(header[0]) else None

    sections: list[Section] = []
    for line in lines:
        if not line:
            continue
        if is_heading(line):
            sections.append(Section(heading=line))
        elif sections:
            sections[-1].lines.append(line)
        else:
            header.append(line)
    return ParsedCv(name=name, headline=headline, contact=header, sections=sections)


def render_cv_pdf(cv_text: str) -> bytes:
    cv = parse_cv(cv_text)
    pdf = FPDF(format="A4")
    pdf.set_margins(LEFT_MARGIN, TOP_MARGIN, LEFT_MARGIN)
    pdf.set_auto_page_break(True, margin=14)
    pdf.set_title(latin1(f"{cv.name} CV"))
    pdf.set_author(latin1(cv.name))
    pdf.add_page()

    pdf.set_font(FONT, "B", 21)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 9, latin1(cv.name), new_x="LMARGIN", new_y="NEXT")
    if cv.headline:
        pdf.set_font(FONT, "", 11.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 6.5, latin1(cv.headline), new_x="LMARGIN", new_y="NEXT", align="L")
    _contact(pdf, cv.contact)

    for section in cv.sections:
        if section.heading.upper() in SECTIONS_LEFT_OUT or not section.lines:
            continue
        _heading(pdf, section.heading)
        _section_body(pdf, section)
    return bytes(pdf.output())


def _looks_like_contact(line: str) -> bool:
    return bool(re.search(r"@|https?://|www\.|\d{5,}|\+\d", line))


def _contact(pdf: FPDF, items: list[str]) -> None:
    pdf.set_font(FONT, "", 9.5)
    pdf.set_text_color(*TEXT)
    short = [item for item in items if len(item) <= 60]
    long = [item for item in items if len(item) > 60]
    pdf.ln(1)
    if short:
        pdf.multi_cell(0, 4.8, latin1("  ·  ".join(short)), new_x="LMARGIN", new_y="NEXT", align="L")
    for item in long:
        pdf.multi_cell(0, 4.8, latin1(item), new_x="LMARGIN", new_y="NEXT", align="L")


def _heading(pdf: FPDF, title: str) -> None:
    if pdf.get_y() > pdf.h - 30:
        pdf.add_page()
    pdf.ln(3.2)
    pdf.set_font(FONT, "B", 10.5)
    pdf.set_text_color(*ACCENT)
    pdf.set_char_spacing(0.6)
    pdf.cell(0, 6, latin1(title.upper()), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_char_spacing(0)
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.35)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(1.6)


def _section_body(pdf: FPDF, section: Section) -> None:
    lines = section.lines
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else ""
        project = PROJECT.match(line)
        dated = DATES_AT_END.match(line)
        if BULLET.match(line):
            _bullet(pdf, BULLET.sub("", line))
        elif project:
            _title_line(pdf, project.group("name"), project.group("stack"), detail_on_right=False)
        elif dated and len(line) <= 140:
            _title_line(pdf, dated.group("title"), dated.group("dates"), detail_on_right=True)
        elif len(line) <= 100 and BULLET.match(following) and not line.endswith("."):
            _title_line(pdf, line, None, detail_on_right=False)
        else:
            _paragraph(pdf, line)


def _title_line(pdf: FPDF, title: str, detail: str | None, *, detail_on_right: bool) -> None:
    if pdf.get_y() > pdf.h - 24:
        pdf.add_page()
    pdf.ln(1.2)
    pdf.set_text_color(*TEXT)
    pdf.set_font(FONT, "B", 10.2)
    title = latin1(title)
    if detail and detail_on_right:
        detail = latin1(detail)
        pdf.set_font(FONT, "", 9.2)
        detail_width = pdf.get_string_width(detail) + 1
        pdf.set_font(FONT, "B", 10.2)
        pdf.cell(pdf.epw - detail_width, 5.4, title)
        pdf.set_font(FONT, "", 9.2)
        pdf.set_text_color(*MUTED)
        pdf.cell(detail_width, 5.4, detail, align="R", new_x="LMARGIN", new_y="NEXT")
        return
    if detail:
        pdf.write(5.4, title)
        pdf.set_font(FONT, "", 9)
        pdf.set_text_color(*MUTED)
        pdf.write(5.4, latin1(f"  ·  {detail}"))
        pdf.ln(5.4)
        return
    pdf.multi_cell(0, 5.4, title, new_x="LMARGIN", new_y="NEXT", align="L")


def _bullet(pdf: FPDF, text: str) -> None:
    indent = 4.5
    pdf.set_font(FONT, "", 9.6)
    pdf.set_text_color(*TEXT)
    y = pdf.get_y()
    pdf.set_fill_color(*ACCENT)
    pdf.ellipse(pdf.l_margin + 1.1, y + 1.85, 1.15, 1.15, style="F")
    pdf.set_x(pdf.l_margin + indent)
    pdf.multi_cell(pdf.epw - indent, 4.6, latin1(text), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(0.5)


def _paragraph(pdf: FPDF, text: str) -> None:
    pdf.set_font(FONT, "", 9.6)
    pdf.set_text_color(*TEXT)
    pdf.multi_cell(0, 4.7, latin1(text), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(0.8)
