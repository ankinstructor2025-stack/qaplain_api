from io import BytesIO
import json
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.firebase import get_firestore_client
from app.routers.data_import_common import (
    DATA_IMPORT_COLLECTION,
    authenticate_user,
    get_storage_bucket,
    normalize_text,
    serialize_value,
)


router = APIRouter(
    prefix="/data-view",
    tags=["data-view"],
)


SUMMARY_EXCLUDED_FIELDS = {
    "data",
    "source_metadata",
}

TEXT_CONTENT_TYPES = {
    "application/json",
    "application/xml",
    "text/xml",
    "text/csv",
    "text/plain",
    "text/html",
}


def get_item_document(item_id: str):
    normalized_item_id = normalize_text(item_id)

    if not normalized_item_id:
        raise HTTPException(
            status_code=400,
            detail="取込データIDが指定されていません。",
        )

    document = (
        get_firestore_client()
        .collection(DATA_IMPORT_COLLECTION)
        .document(normalized_item_id)
        .get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail="取込データが見つかりません。",
        )

    return document


def build_effective_file_name(item: dict, item_id: str) -> str:
    file_name = normalize_text(item.get("file_name"))
    if file_name:
        return file_name

    extension = normalize_text(item.get("extension")) or "bin"
    item_type = normalize_text(item.get("item_type")) or "data"
    display_name = (
        normalize_text(item.get("display_name"))
        or normalize_text(item.get("title"))
    )

    if display_name:
        safe_name = display_name.replace("/", "_").replace("\\", "_")
        safe_name = safe_name.strip(" .")[:100]
        if safe_name:
            return f"{safe_name}.{extension}"

    source_index = item.get("source_index")
    try:
        sequence = int(source_index) + 1
        return f"{item_type}_{sequence:04d}.{extension}"
    except (TypeError, ValueError):
        return f"{item_type}_{item_id}.{extension}"


def serialize_item_summary(document) -> dict:
    source = document.to_dict() or {}
    item = {
        key: value
        for key, value in source.items()
        if key not in SUMMARY_EXCLUDED_FIELDS
    }

    item_id = normalize_text(item.get("item_id")) or document.id
    item["item_id"] = item_id
    item["file_name"] = build_effective_file_name(item, item_id)

    return serialize_value(item)


def serialize_item_detail(document) -> dict:
    item = document.to_dict() or {}
    item_id = normalize_text(item.get("item_id")) or document.id
    item["item_id"] = item_id
    item.pop("data", None)
    item.pop("source_metadata", None)
    item["file_name"] = build_effective_file_name(item, item_id)
    return serialize_value(item)


def get_item_sort_key(item: dict):
    level = item.get("level")
    source_index = item.get("source_index")

    try:
        normalized_level = int(level)
    except (TypeError, ValueError):
        normalized_level = 999

    try:
        normalized_index = int(source_index)
    except (TypeError, ValueError):
        normalized_index = 999999999

    return (
        normalized_level,
        normalized_index,
        normalize_text(item.get("created_at")),
        normalize_text(item.get("item_id")),
    )


def build_summary(items: list[dict]) -> dict:
    item_type_counts: dict[str, int] = {}
    total_size_bytes = 0

    visible_items = [
        item
        for item in items
        if normalize_text(item.get("item_type")) != "raw_response"
    ]

    for item in visible_items:
        item_type = normalize_text(
            item.get("item_type", "unknown")
        ) or "unknown"

        item_type_counts[item_type] = (
            item_type_counts.get(item_type, 0) + 1
        )

        try:
            total_size_bytes += int(item.get("size_bytes") or 0)
        except (TypeError, ValueError):
            pass

    parent_count = sum(
        1
        for item in visible_items
        if normalize_text(item.get("item_type")) == "parent"
    )

    child_count = sum(
        1
        for item in visible_items
        if normalize_text(item.get("parent_id"))
    )

    file_count = sum(
        1
        for item in visible_items
        if normalize_text(item.get("gcs_path"))
    )

    return {
        "total_count": len(visible_items),
        "parent_count": parent_count,
        "child_count": child_count,
        "file_count": file_count,
        "total_size_bytes": total_size_bytes,
        "item_type_counts": item_type_counts,
    }


def read_storage_content(item: dict) -> tuple[bytes, str]:
    gcs_path = normalize_text(item.get("gcs_path"))

    if not gcs_path:
        raise HTTPException(
            status_code=404,
            detail="保存ファイルがありません。",
        )

    blob = get_storage_bucket().blob(gcs_path)

    try:
        if not blob.exists():
            raise HTTPException(
                status_code=404,
                detail="Cloud Storageにファイルがありません。",
            )

        content = blob.download_as_bytes()

    except HTTPException:
        raise

    except Exception as error:
        print(
            "Cloud Storage read error: "
            f"{type(error).__name__}: {error}"
        )
        raise HTTPException(
            status_code=500,
            detail="保存ファイルを読み込めませんでした。",
        )

    content_type = (
        normalize_text(item.get("content_type"))
        or normalize_text(blob.content_type)
        or "application/octet-stream"
    )

    return content, content_type


@router.get("/items")
def list_items(
    data_source_id: str = Query(..., min_length=1),
    authorization: str = Header(...),
):
    authenticate_user(authorization)

    normalized_data_source_id = normalize_text(data_source_id)

    documents = (
        get_firestore_client()
        .collection(DATA_IMPORT_COLLECTION)
        .where(
            "data_source_id",
            "==",
            normalized_data_source_id,
        )
        .stream()
    )

    items = [
        serialize_item_summary(document)
        for document in documents
        if not (document.to_dict() or {}).get("deleted", False)
    ]

    items.sort(key=get_item_sort_key)

    batch_ids = sorted({
        normalize_text(item.get("batch_id"))
        for item in items
        if normalize_text(item.get("batch_id"))
    })

    latest_batch_id = ""

    if items:
        latest_item = max(
            items,
            key=lambda item: normalize_text(
                item.get("created_at") or item.get("updated_at")
            ),
        )
        latest_batch_id = normalize_text(latest_item.get("batch_id"))

    latest_items = (
        [
            item
            for item in items
            if normalize_text(item.get("batch_id")) == latest_batch_id
        ]
        if latest_batch_id
        else items
    )

    processing_pattern = (
        normalize_text(latest_items[0].get("processing_pattern"))
        if latest_items
        else ""
    )

    return {
        "data_source_id": normalized_data_source_id,
        "data_source_name": (
            latest_items[0].get("data_source_name", "")
            if latest_items
            else ""
        ),
        "processing_pattern": processing_pattern,
        "batch_ids": batch_ids,
        "latest_batch_id": latest_batch_id,
        "summary": build_summary(latest_items),
        "items": latest_items,
    }


@router.get("/items/{item_id}")
def get_item(
    item_id: str,
    authorization: str = Header(...),
):
    authenticate_user(authorization)
    document = get_item_document(item_id)
    item = document.to_dict() or {}
    content, content_type = read_storage_content(item)

    content_text = None
    content_json = None

    if content_type in TEXT_CONTENT_TYPES:
        content_text = content.decode("utf-8", errors="replace")

        if content_type == "application/json":
            try:
                content_json = json.loads(content_text)
            except json.JSONDecodeError:
                pass

    return {
        "item": serialize_item_detail(document),
        "content_available": content_text is not None,
        "content_text": content_text,
        "content_json": content_json,
    }


@router.get("/items/{item_id}/download")
def download_item(
    item_id: str,
    authorization: str = Header(...),
):
    authenticate_user(authorization)
    document = get_item_document(item_id)
    item = document.to_dict() or {}
    content, content_type = read_storage_content(item)

    file_name = build_effective_file_name(item, document.id)
    encoded_file_name = quote(file_name)

    return StreamingResponse(
        BytesIO(content),
        media_type=content_type,
        headers={
            "Content-Disposition": (
                "attachment; "
                f"filename*=UTF-8''{encoded_file_name}"
            )
        },
    )
