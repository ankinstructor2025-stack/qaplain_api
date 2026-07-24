import json
from typing import Any

from fastapi import HTTPException

from app.routers.data_raw_analysis_common import (
    create_record,
)
from app.routers.data_import_common import (
    normalize_text,
)


TITLE_KEYS = (
    "title",
    "name",
    "subject",
    "id",
    "record_id",
    "speechID",
    "issueID",
)


def analyze_json(
    content: bytes,
) -> tuple[list[dict], dict]:
    """
    JSONを解析し、次の形に分解する。

    - JSONオブジェクト直下の通常項目:
      raw_documentsの親ドキュメントへそのまま保存する。
    - JSONオブジェクト直下の配列:
      recordsサブコレクションへ1要素ずつ保存する。
    - 配列要素がオブジェクトの場合:
      structured_data配下ではなく、レコード直下へ項目を展開する。

    戻り値:
        records:
            recordsサブコレクションへ登録するデータ。
        document_data:
            raw_documentsの親ドキュメントへ追加するデータ。
    """
    data = load_json(content)

    if isinstance(data, dict):
        return analyze_json_object(data)

    if isinstance(data, list):
        records = create_array_records(
            root_name="root",
            values=data,
            start_sequence=1,
        )

        return records, {
            "json_root_type": "array",
            "json_root_name": "root",
            "json_array_count": 1,
        }

    return [], {
        "json_root_type": "value",
        "value": data,
    }


def load_json(
    content: bytes,
) -> Any:
    try:
        return json.loads(
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


def analyze_json_object(
    data: dict,
) -> tuple[list[dict], dict]:
    """
    オブジェクト直下の配列だけをrecordsへ分離し、
    それ以外の項目は親ドキュメントへそのまま保存する。
    """
    document_data = {}
    array_items = []

    for key, value in data.items():
        if isinstance(value, list):
            array_items.append(
                (
                    normalize_text(key) or "items",
                    value,
                )
            )
            continue

        document_data[key] = value

    document_data["json_root_type"] = "object"
    document_data["json_array_count"] = len(array_items)

    if not array_items:
        return [], document_data

    records = []
    sequence = 1

    for root_name, values in array_items:
        created_records = create_array_records(
            root_name=root_name,
            values=values,
            start_sequence=sequence,
        )

        records.extend(created_records)
        sequence += len(created_records)

    document_data["json_array_names"] = [
        root_name
        for root_name, _ in array_items
    ]

    if len(array_items) == 1:
        document_data["json_root_name"] = (
            array_items[0][0]
        )

    return records, document_data


def create_array_records(
    root_name: str,
    values: list,
    start_sequence: int,
) -> list[dict]:
    records = []

    for offset, value in enumerate(values):
        sequence = start_sequence + offset
        array_index = offset

        record = create_json_record(
            root_name=root_name,
            value=value,
            sequence=sequence,
            array_index=array_index,
        )

        records.append(record)

    return records


def create_json_record(
    root_name: str,
    value: Any,
    sequence: int,
    array_index: int,
) -> dict:
    title = get_record_title(
        value=value,
        fallback=(
            f"{root_name}[{array_index}]"
        ),
    )

    record_type = (
        "json_object"
        if isinstance(value, dict)
        else "json_array"
        if isinstance(value, list)
        else "json_value"
    )

    record = create_record(
        record_type=record_type,
        title=title,
        sequence=sequence,
        structured_data=value,
        content=json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        metadata={
            "root_name": root_name,
            "array_index": array_index,
        },
    )

    # JSONオブジェクトの項目をrecords直下へ展開する。
    # structured_dataにも同じ内容を重複保存しない。
    if isinstance(value, dict):
        record.pop(
            "structured_data",
            None,
        )

        for key, child_value in value.items():
            if key in record:
                record[f"json_{key}"] = child_value
            else:
                record[key] = child_value

    return record


def get_record_title(
    value: Any,
    fallback: str,
) -> str:
    if not isinstance(value, dict):
        return fallback

    for key in TITLE_KEYS:
        title = normalize_text(
            value.get(key)
        )

        if title:
            return title

    return fallback
