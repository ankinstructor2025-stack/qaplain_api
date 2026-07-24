from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from app.core.firebase import get_firestore_client


router = APIRouter(
    prefix="/raw-data-view",
    tags=["raw_data_view"],
)


DATA_SOURCE_COLLECTION = "data_sources"
RAW_DOCUMENT_COLLECTION = "raw_documents"
RECORD_COLLECTION = "records"


def get_db():
    return get_firestore_client()


def serialize_value(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {
            key: serialize_value(child_value)
            for key, child_value in value.items()
        }

    if isinstance(value, list):
        return [
            serialize_value(child_value)
            for child_value in value
        ]

    return value


def get_display_name(data: dict) -> str:
    return str(
        data.get("display_name")
        or data.get("file_name")
        or data.get("title")
        or data.get("name")
        or ""
    )


def serialize_document(document) -> dict:
    data = document.to_dict() or {}
    serialized = serialize_value(data)

    serialized["document_id"] = document.id
    serialized["display_name"] = get_display_name(data)

    if "record_count" not in serialized:
        serialized["record_count"] = data.get(
            "child_count",
            data.get("item_count", 0),
        )

    return serialized


def serialize_record(document) -> dict:
    data = document.to_dict() or {}
    serialized = serialize_value(data)

    serialized["record_id"] = document.id
    serialized.setdefault("title", "")
    serialized.setdefault("content", "")
    serialized.setdefault("sequence", 0)
    serialized.setdefault("start_page", "")
    serialized.setdefault("end_page", "")
    serialized.setdefault("metadata", {})

    return serialized


def get_sort_datetime(data: dict):
    value = (
        data.get("analyzed_at")
        or data.get("updated_at")
        or data.get("created_at")
    )

    if isinstance(value, datetime):
        return value

    return datetime.min


@router.get("/documents")
def list_documents(
    data_source_id: str = Query(min_length=1),
    limit: int = Query(default=200, ge=1, le=1000),
):
    db = get_db()

    query = (
        db.collection(DATA_SOURCE_COLLECTION)
        .document(data_source_id)
        .collection(RAW_DOCUMENT_COLLECTION)
        .limit(limit)
    )

    documents = list(query.stream())

    documents.sort(
        key=lambda document: get_sort_datetime(
            document.to_dict() or {}
        ),
        reverse=True,
    )

    return {
        "documents": [
            serialize_document(document)
            for document in documents
        ]
    }


@router.get("/documents/{document_id}/records")
def list_records(
    document_id: str,
    data_source_id: str = Query(min_length=1),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    db = get_db()

    document_reference = (
        db.collection(DATA_SOURCE_COLLECTION)
        .document(data_source_id)
        .collection(RAW_DOCUMENT_COLLECTION)
        .document(document_id)
    )

    parent_document = document_reference.get()

    if not parent_document.exists:
        raise HTTPException(
            status_code=404,
            detail="親データが見つかりません。",
        )

    record_documents = list(
        document_reference
        .collection(RECORD_COLLECTION)
        .limit(limit)
        .stream()
    )

    records = [
        serialize_record(document)
        for document in record_documents
    ]

    def sort_key(record: dict):
        try:
            sequence = int(record.get("sequence", 0) or 0)
        except (TypeError, ValueError):
            sequence = 0

        return (
            sequence,
            record.get("record_id", ""),
        )

    records.sort(key=sort_key)

    return {
        "document_id": document_id,
        "records": records,
    }
