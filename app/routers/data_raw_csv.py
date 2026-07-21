import csv
import io
import json

from app.routers.data_raw_analysis_common import (
    create_record,
    detect_text_encoding,
)
from app.routers.data_import_common import (
    normalize_text,
)


def analyze_csv(
    content: bytes,
) -> tuple[list[dict], dict]:
    encoding = detect_text_encoding(
        content
    )

    text = content.decode(
        encoding,
        errors="replace",
    )

    try:
        dialect = csv.Sniffer().sniff(
            text[:8192],
            delimiters=",\t;",
        )
    except Exception:
        dialect = csv.excel

    reader = csv.DictReader(
        io.StringIO(text),
        dialect=dialect,
    )

    headers = [
        normalize_text(header)
        for header in (
            reader.fieldnames or []
        )
    ]

    records = []

    for index, row in enumerate(
        reader,
        start=1,
    ):
        row_data = {
            normalize_text(key):
                normalize_text(value)
            for key, value in row.items()
            if key is not None
        }

        records.append(
            create_record(
                record_type=
                    "csv_row",
                title=
                    f"行 {index + 1}",
                sequence=
                    index,
                structured_data=
                    row_data,
                content=json.dumps(
                    row_data,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                metadata={
                    "row_number":
                        index + 1,
                    "headers":
                        headers,
                    "encoding":
                        encoding,
                },
            )
        )

    return records, {
        "headers":
            headers,
        "encoding":
            encoding,
    }
