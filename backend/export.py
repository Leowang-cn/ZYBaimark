from __future__ import annotations

import csv
import io
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font

from .pipeline.extract import fmt_ts

HEADER = ["视频ID", "视频名称", "问题时间点", "问题", "答案"]
COL_TIME, COL_Q, COL_A = 2, 3, 4
SHEET = "问题抽取"


class ExportError(Exception):
    pass


def build_rows(videos_marks: list[tuple[str, str, list]]) -> list[list[str]]:
    rows = []
    for material_id, name, marks in sorted(videos_marks, key=lambda x: (x[1], x[0])):
        for m in sorted(marks, key=lambda m: m.time_s):
            rows.append([material_id, name, fmt_ts(m.time_s), m.question.strip(), m.answer.strip()])
    return rows


def validate_rows(rows: list[list[str]]) -> None:
    problems = []
    seen = set()
    for i, r in enumerate(rows, 2):
        if not r[COL_Q]:
            problems.append(f"第{i}行问题为空")
        if not r[COL_A]:
            problems.append(f"第{i}行答案为空")
        if not re.fullmatch(r"\d{2}:\d{2}:\d{2}\.\d{3}", r[COL_TIME]):
            problems.append(f"第{i}行时间格式错误")
        if re.fullmatch(r"[A-H]\s*选项.*|[A-H]\s*[（(，,].+", r[COL_A]):
            problems.append(f"第{i}行答案含冗余选项表达")
        if tuple(r) in seen:
            problems.append(f"第{i}行重复")
        seen.add(tuple(r))
    if problems:
        raise ExportError("；".join(problems[:10]))


def to_xlsx(rows: list[list[str]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True)
    for col, width in zip("ABCDE", [22, 48, 16, 70, 20]):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.number_format = "@"
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    back = list(load_workbook(io.BytesIO(data))[SHEET].iter_rows(values_only=True))
    if list(back[0]) != HEADER or len(back) != len(rows) + 1 or any(len(r) != len(HEADER) for r in back):
        raise ExportError("Excel 回读校验失败")
    return data


def to_csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows([HEADER, *rows])
    data = buf.getvalue().encode("utf-8-sig")
    back = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    if back[0] != HEADER or len(back) != len(rows) + 1:
        raise ExportError("CSV 回读校验失败")
    return data
