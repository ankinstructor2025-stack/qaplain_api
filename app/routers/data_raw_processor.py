import uuid
from pathlib import PurePosixPath

from fastapi import HTTPException
from google.cloud import firestore

from app.core.firebase import (
    get_firestore_client,
)
from app.routers.data_import_common import (
    get_storage_bucket,
    normalize_extension,
    normalize_text,
    now_iso,
)
from app.routers.data_raw_analysis import (
    analyze_file,
)
from app.routers.data_raw_analysis_common import (
    safe_value,
)


SUPPORTED_FILE_TYPE_COLLECTION = (
    "supported_file_types"
)
DATA_SOURCE_COLLECTION = (
    "data_sources"
)
UPLOADED_FILE_COLLECTION = (
    "uploaded_files"
)
DATA_IMPORT_COLLECTION = (
    "data_import_items"
)

RAW_DOCUMENT_COLLECTION = (
    "raw_documents"
)
RAW_RECORD_SUBCOLLECTION = (
    "records"
)

BATCH_WRITE_LIMIT = 400


def validate_supported_extension(
    extension: str,
) -> tuple[str, str]:
    normalized_extension = (
        normalize_extension(extension)
    )

    if not normalized_extension:
        return (
            "",
            "extension_not_found",
        )

    document = (
        get_firestore_client()
        .collection(
            SUPPORTED_FILE_TYPE_COLLECTION
        )
        .document(
            normalized_extension
        )
        .get()
    )

    if not document.exists:
        return (
            normalized_extension,
            "extension_not_registered",
        )

    data = document.to_dict() or {}

    if data.get("enabled", True) is False:
        return (
            normalized_extension,
            "extension_disabled",
        )

    return (
        normalized_extension,
        "",
    )


def get_selected_file_extensions(
    data_source_id: str,
) -> set[str]:
    normalized_data_source_id = (
        normalize_text(data_source_id)
    )

    if not normalized_data_source_id:
        return set()

    document = (
        get_firestore_client()
        .collection(
            DATA_SOURCE_COLLECTION
        )
        .document(
            normalized_data_source_id
        )
        .get()
    )

    if not document.exists:
        return set()

    data = document.to_dict() or {}

    return {
        normalize_extension(extension)
        for extension in (
            data.get(
                "file_extensions",
                [],
            )
            or []
        )
        if normalize_extension(extension)
    }


def skip_source_file(
    *,
    source: dict,
    extension: str,
    reason: str,
    user: dict,
) -> dict:
    print(
        "[DATA_RAW_SKIP] "
        f"data_source_id="
        f"{source.get('data_source_id', '')}, "
        f"source_type="
        f"{source.get('source_type', '')}, "
        f"source_id="
        f"{source.get('source_id', '')}, "
        f"file_name="
        f"{source.get('file_name', '')}, "
        f"extension="
        f"{extension or '(empty)'}, "
        f"reason={reason}"
    )

    (
        get_firestore_client()
        .collection(
            source["collection_name"]
        )
        .document(
            source["source_id"]
        )
        .set({
            "analysis_status":
                "completed",
            "analysis_result":
                "skipped",
            "analysis_skip_reason":
                reason,
            "analysis_extension":
                extension,
            "analysis_record_count":
                0,
            "analyzed_at":
                now_iso(),
            "analyzed_by":
                user.get("email", ""),
        }, merge=True)
    )

    return {
        "status":
            "skipped",
        "source_type":
            source["source_type"],
        "source_id":
            source["source_id"],
        "extension":
            extension,
        "reason":
            reason,
        "record_count":
            0,
    }


def get_source_file(
    source_type: str,
    source_id: str,
) -> dict:
    normalized_source_type = (
        normalize_text(source_type)
    )
    normalized_source_id = (
        normalize_text(source_id)
    )

    collection_map = {
        "uploaded_file":
            UPLOADED_FILE_COLLECTION,
        "api_import":
            DATA_IMPORT_COLLECTION,
    }

    collection_name = collection_map.get(
        normalized_source_type
    )

    if not collection_name:
        raise HTTPException(
            status_code=400,
            detail=(
                "source_typeはuploaded_file"
                "またはapi_importを指定してください。"
            ),
        )

    document = (
        get_firestore_client()
        .collection(collection_name)
        .document(normalized_source_id)
        .get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "解析対象ファイルが"
                "見つかりません。"
            ),
        )

    data = document.to_dict() or {}

    if data.get("deleted", False):
        raise HTTPException(
            status_code=400,
            detail="削除済みのファイルです。",
        )

    gcs_path = normalize_text(
        data.get("gcs_path")
    )

    extension = normalize_extension(
        data.get("extension")
    )

    file_name = normalize_text(
        data.get("file_name")
    )

    if not extension and "." in file_name:
        extension = normalize_extension(
            PurePosixPath(
                file_name
            ).suffix
        )

    if not gcs_path:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cloud Storageの保存先が"
                "登録されていません。"
            ),
        )

    return {
        "source_type":
            normalized_source_type,
        "source_id":
            normalized_source_id,
        "collection_name":
            collection_name,
        "data_source_id":
            normalize_text(
                data.get(
                    "data_source_id"
                )
            ),
        "data_source_name":
            normalize_text(
                data.get(
                    "data_source_name"
                )
            ),
        "tenant_id":
            normalize_text(
                data.get("tenant_id")
            ),
        "file_name": (
            file_name
            or PurePosixPath(
                gcs_path
            ).name
        ),
        "content_type":
            normalize_text(
                data.get("content_type")
            ),
        "extension":
            extension,
        "bucket_name":
            normalize_text(
                data.get("bucket_name")
            ),
        "gcs_path":
            gcs_path,
        "gcs_uri":
            normalize_text(
                data.get("gcs_uri")
            ),
    }


def download_source(
    source: dict,
) -> bytes:
    try:
        blob = get_storage_bucket().blob(
            source["gcs_path"]
        )

        if not blob.exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    "Cloud Storageに"
                    "ファイルがありません。"
                ),
            )

        return blob.download_as_bytes()

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "Cloud Storageから"
                "ファイルを取得できません。"
                f" {type(error).__name__}: {error}"
            ),
        )


def delete_existing_records(
    document_reference,
) -> None:
    db = get_firestore_client()

    while True:
        documents = list(
            document_reference
            .collection(
                RAW_RECORD_SUBCOLLECTION
            )
            .limit(
                BATCH_WRITE_LIMIT
            )
            .stream()
        )

        if not documents:
            return

        batch = db.batch()

        for document in documents:
            batch.delete(
                document.reference
            )

        batch.commit()


def write_records(
    document_reference,
    document_id: str,
    records: list[dict],
    source: dict,
    user: dict,
) -> None:
    db = get_firestore_client()
    batch = db.batch()
    operation_count = 0

    for index, record in enumerate(
        records,
        start=1,
    ):
        record_id = (
            f"{index:08d}_"
            f"{uuid.uuid4().hex[:8]}"
        )

        record_data = {
            "record_id":
                record_id,
            "document_id":
                document_id,
            "source_type":
                source["source_type"],
            "source_id":
                source["source_id"],
            "data_source_id":
                source["data_source_id"],
            "tenant_id":
                source["tenant_id"],
            **record,
            "created_at":
                now_iso(),
            "created_by":
                user.get("email", ""),
        }

        reference = (
            document_reference
            .collection(
                RAW_RECORD_SUBCOLLECTION
            )
            .document(record_id)
        )

        batch.set(
            reference,
            record_data,
        )

        operation_count += 1

        if (
            operation_count
            >= BATCH_WRITE_LIMIT
        ):
            batch.commit()
            batch = db.batch()
            operation_count = 0

    if operation_count:
        batch.commit()


def process_source_file(
    *,
    source_type: str,
    source_id: str,
    user: dict,
    overwrite: bool = True,
) -> dict:
    source = get_source_file(
        source_type,
        source_id,
    )

    extension, extension_reason = (
        validate_supported_extension(
            source["extension"]
        )
    )

    if extension_reason:
        return skip_source_file(
            source=source,
            extension=extension,
            reason=extension_reason,
            user=user,
        )

    selected_extensions = (
        get_selected_file_extensions(
            source["data_source_id"]
        )
    )

    if extension not in selected_extensions:
        return skip_source_file(
            source=source,
            extension=extension,
            reason="extension_not_selected",
            user=user,
        )

    content = download_source(source)

    records, analysis_metadata = (
        analyze_file(
            extension,
            content,
        )
    )

    document_id = (
        f"{source['source_type']}_"
        f"{source['source_id']}"
    )

    db = get_firestore_client()

    document_reference = (
        db.collection(
            RAW_DOCUMENT_COLLECTION
        )
        .document(document_id)
    )

    existing_document = (
        document_reference.get()
    )

    if (
        existing_document.exists
        and not overwrite
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "このファイルは既に"
                "Firestoreへ登録されています。"
            ),
        )

    if existing_document.exists:
        delete_existing_records(
            document_reference
        )

    now = now_iso()

    document_data = {
        "document_id":
            document_id,
        "source_type":
            source["source_type"],
        "source_id":
            source["source_id"],
        "data_source_id":
            source["data_source_id"],
        "data_source_name":
            source["data_source_name"],
        "tenant_id":
            source["tenant_id"],
        "file_name":
            source["file_name"],
        "extension":
            extension,
        "content_type":
            source["content_type"],
        "bucket_name":
            source["bucket_name"],
        "gcs_path":
            source["gcs_path"],
        "gcs_uri":
            source["gcs_uri"],
        "size_bytes":
            len(content),
        "record_count":
            len(records),
        "analysis_metadata":
            safe_value(
                analysis_metadata
            ),
        "status":
            "processed",
        "processed_at":
            now,
        "processed_by":
            user.get("email", ""),
        "updated_at":
            now,
        "updated_by":
            user.get("email", ""),
    }

    if not existing_document.exists:
        document_data.update({
            "created_at":
                now,
            "created_by":
                user.get("email", ""),
        })

    document_reference.set(
        document_data,
        merge=True,
    )

    try:
        write_records(
            document_reference,
            document_id,
            records,
            source,
            user,
        )

    except Exception as error:
        document_reference.set({
            "status":
                "failed",
            "error_message":
                str(error),
            "updated_at":
                now_iso(),
        }, merge=True)

        raise

    source_reference = (
        db.collection(
            source["collection_name"]
        )
        .document(
            source["source_id"]
        )
    )

    source_reference.set({
        "raw_document_id":
            document_id,
        "analysis_status":
            "completed",
        "analysis_result":
            "processed",
        "analysis_skip_reason":
            firestore.DELETE_FIELD,
        "analysis_extension":
            extension,
        "analysis_record_count":
            len(records),
        "analyzed_at":
            now_iso(),
        "analyzed_by":
            user.get("email", ""),
    }, merge=True)

    return {
        "status":
            "completed",
        "document_id":
            document_id,
        "source_type":
            source["source_type"],
        "source_id":
            source["source_id"],
        "extension":
            extension,
        "record_count":
            len(records),
        "split_method":
            analysis_metadata.get(
                "split_method"
            ),
    }
