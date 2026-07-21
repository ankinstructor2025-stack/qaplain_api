import math
import re
from datetime import date, datetime
from typing import Any

from app.routers.data_import_common import (
    normalize_text,
)


MAX_TEXT_LENGTH = 180_000

HEADING_PATTERNS = (
    re.compile(
        r"^\s*第[0-9０-９一二三四五六七八九十百千]+"
        r"[章節款項]\s*.+$"
    ),
    re.compile(
        r"^\s*[0-9０-９]+"
        r"(?:[.\-．－][0-9０-９]+)*"
        r"[.．、)]?\s+.+$"
    ),
    re.compile(
        r"^\s*[（(]?"
        r"[0-9０-９一二三四五六七八九十]+"
        r"[）)]\s*.+$"
    ),
    re.compile(
        r"^\s*[■◆●○◎◇□]\s*.+$"
    ),
)


def safe_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, (str, int, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): safe_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            safe_value(item)
            for item in value
        ]

    return str(value)


def split_long_text(
    text: str,
    max_length: int = MAX_TEXT_LENGTH,
) -> list[str]:
    normalized = normalize_text(text)

    if not normalized:
        return [""]

    if len(normalized) <= max_length:
        return [normalized]

    chunks = []
    current = []

    for paragraph in re.split(
        r"\n{2,}",
        normalized,
    ):
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        candidate = "\n\n".join(
            current + [paragraph]
        )

        if (
            current
            and len(candidate) > max_length
        ):
            chunks.append(
                "\n\n".join(current)
            )
            current = [paragraph]
        else:
            current.append(paragraph)

        while (
            current
            and len(current[0]) > max_length
        ):
            long_text = current.pop(0)
            chunks.append(
                long_text[:max_length]
            )
            remaining = long_text[max_length:]

            if remaining:
                current.insert(
                    0,
                    remaining,
                )

    if current:
        chunks.append(
            "\n\n".join(current)
        )

    return chunks


def looks_like_heading(
    line: str,
) -> bool:
    value = normalize_text(line)

    if not value:
        return False

    if len(value) > 120:
        return False

    if value.endswith(
        ("。", "、", "，", ",")
    ):
        return False

    return any(
        pattern.match(value)
        for pattern in HEADING_PATTERNS
    )


def split_lines_by_heading(
    lines: list[
        tuple[int | None, str]
    ],
) -> list[dict]:
    sections = []
    current_title = ""
    current_lines = []
    start_page = None
    end_page = None

    def flush() -> None:
        nonlocal current_title
        nonlocal current_lines
        nonlocal start_page
        nonlocal end_page

        content = "\n".join(
            line
            for _, line in current_lines
        ).strip()

        if not content and not current_title:
            return

        sections.append({
            "title":
                current_title,
            "content":
                content,
            "start_page":
                start_page,
            "end_page":
                end_page,
        })

        current_title = ""
        current_lines = []
        start_page = None
        end_page = None

    for page_number, line in lines:
        clean_line = line.strip()

        if not clean_line:
            if current_lines:
                current_lines.append(
                    (page_number, "")
                )
            continue

        if looks_like_heading(
            clean_line
        ):
            flush()
            current_title = clean_line
            start_page = page_number
            end_page = page_number
            continue

        if start_page is None:
            start_page = page_number

        end_page = page_number
        current_lines.append(
            (page_number, clean_line)
        )

    flush()

    return sections


def create_record(
    *,
    record_type: str,
    title: str = "",
    content: str = "",
    sequence: int = 0,
    metadata: dict | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
    structured_data: Any = None,
) -> dict:
    return {
        "record_type":
            record_type,
        "title":
            normalize_text(title),
        "content":
            normalize_text(content),
        "sequence":
            sequence,
        "start_page":
            start_page,
        "end_page":
            end_page,
        "structured_data":
            safe_value(structured_data),
        "metadata":
            safe_value(metadata or {}),
    }


def detect_text_encoding(
    content: bytes,
) -> str:
    for encoding in (
        "utf-8-sig",
        "utf-8",
        "cp932",
    ):
        try:
            content.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue

    return "utf-8"
