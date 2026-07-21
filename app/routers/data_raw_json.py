import json

from fastapi import HTTPException

from app.routers.data_raw_analysis_common import (
    create_record,
)
from app.routers.data_import_common import (
    normalize_text,
)


def analyze_json(
    content: bytes,
) -> tuple[list[dict], dict]:
    try:
        data = json.loads(
            content.decode(
                "utf-8-sig"
            )
        )
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "JSONファイルを解析できません。"
                f" {type(error).__name__}: {error}"
            ),
        )

    if isinstance(data, list):
        values = data
        root_name = "root"

    elif isinstance(data, dict):
        list_candidates = [
            (key, value)
            for key, value in data.items()
            if isinstance(value, list)
        ]

        if len(list_candidates) == 1:
            root_name, values = (
                list_candidates[0]
            )
        else:
            return [
                create_record(
                    record_type=
                        "json_object",
                    title=
                        "root",
                    sequence=
                        1,
                    structured_data=
                        data,
                    content=json.dumps(
                        data,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            ], {}

    else:
        return [
            create_record(
                record_type=
                    "json_value",
                title=
                    "root",
                sequence=
                    1,
                structured_data=
                    data,
                content=json.dumps(
                    data,
                    ensure_ascii=False,
                ),
            )
        ], {}

    records = []

    for index, value in enumerate(
        values,
        start=1,
    ):
        title = ""

        if isinstance(value, dict):
            for key in (
                "title",
                "name",
                "subject",
                "id",
                "record_id",
            ):
                title = normalize_text(
                    value.get(key)
                )

                if title:
                    break

        records.append(
            create_record(
                record_type=
                    "json_item",
                title=(
                    title
                    or f"{root_name}[{index - 1}]"
                ),
                sequence=
                    index,
                structured_data=
                    value,
                content=json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                metadata={
                    "root_name":
                        root_name,
                    "array_index":
                        index - 1,
                },
            )
        )

    return records, {
        "root_name": root_name,
    }
