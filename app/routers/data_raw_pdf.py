import io

from fastapi import HTTPException
from pypdf import PdfReader

from app.routers.data_raw_analysis_common import (
    create_record,
    safe_value,
    split_lines_by_heading,
    split_long_text,
)


def analyze_pdf(
    content: bytes,
) -> tuple[list[dict], dict]:
    try:
        reader = PdfReader(
            io.BytesIO(content)
        )
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "PDFファイルを解析できません。"
                f" {type(error).__name__}: {error}"
            ),
        )

    page_lines = []
    page_texts = []

    for page_number, page in enumerate(
        reader.pages,
        start=1,
    ):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""

        page_texts.append(text)

        for line in text.splitlines():
            page_lines.append(
                (page_number, line)
            )

    sections = split_lines_by_heading(
        page_lines
    )

    heading_count = sum(
        1
        for section in sections
        if section["title"]
    )

    records = []

    if heading_count >= 2:
        for section in sections:
            chunks = split_long_text(
                section["content"]
            )

            for chunk_index, chunk in enumerate(
                chunks,
                start=1,
            ):
                title = section["title"]

                if len(chunks) > 1:
                    title = (
                        f"{title} "
                        f"({chunk_index}/{len(chunks)})"
                    )

                records.append(
                    create_record(
                        record_type=
                            "pdf_section",
                        title=
                            title,
                        content=
                            chunk,
                        sequence=
                            len(records) + 1,
                        start_page=
                            section["start_page"],
                        end_page=
                            section["end_page"],
                        metadata={
                            "split_method":
                                "heading",
                        },
                    )
                )
    else:
        for page_number, text in enumerate(
            page_texts,
            start=1,
        ):
            chunks = split_long_text(text)

            for chunk_index, chunk in enumerate(
                chunks,
                start=1,
            ):
                title = f"{page_number}ページ"

                if len(chunks) > 1:
                    title = (
                        f"{title} "
                        f"({chunk_index}/{len(chunks)})"
                    )

                records.append(
                    create_record(
                        record_type=
                            "pdf_page",
                        title=
                            title,
                        content=
                            chunk,
                        sequence=
                            len(records) + 1,
                        start_page=
                            page_number,
                        end_page=
                            page_number,
                        metadata={
                            "split_method":
                                "page",
                        },
                    )
                )

    return records, {
        "page_count":
            len(reader.pages),
        "pdf_metadata":
            safe_value(
                dict(
                    reader.metadata or {}
                )
            ),
        "split_method": (
            "heading"
            if heading_count >= 2
            else "page"
        ),
    }
