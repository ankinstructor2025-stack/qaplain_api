from app.routers.data_raw_analysis_common import (
    create_record,
    detect_text_encoding,
    split_lines_by_heading,
    split_long_text,
)


def analyze_txt(
    content: bytes,
) -> tuple[list[dict], dict]:
    encoding = detect_text_encoding(
        content
    )

    text = content.decode(
        encoding,
        errors="replace",
    )

    lines = [
        (None, line)
        for line in text.splitlines()
    ]

    sections = split_lines_by_heading(
        lines
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
                            "text_section",
                        title=
                            title,
                        content=
                            chunk,
                        sequence=
                            len(records) + 1,
                        metadata={
                            "encoding":
                                encoding,
                            "split_method":
                                "heading",
                        },
                    )
                )

        return records, {
            "encoding":
                encoding,
            "split_method":
                "heading",
        }

    for index, chunk in enumerate(
        split_long_text(text),
        start=1,
    ):
        records.append(
            create_record(
                record_type=
                    "text_chunk",
                title=
                    f"本文 {index}",
                content=
                    chunk,
                sequence=
                    index,
                metadata={
                    "encoding":
                        encoding,
                    "split_method":
                        "fixed_length",
                },
            )
        )

    return records, {
        "encoding":
            encoding,
        "split_method":
            "fixed_length",
    }
