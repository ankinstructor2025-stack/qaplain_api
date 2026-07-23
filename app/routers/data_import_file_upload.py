import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from firebase_admin import firestore

from app.routers.data_import_common import (
    BUCKET_NAME,
    get_data_import_collection,
    get_data_source,
    get_storage_bucket,
    normalize_extension,
    normalize_text,
)



def get_file_extension(file_name: str) -> str:
    return normalize_extension(Path(file_name).suffix)


def sanitize_file_name(file_name: str) -> str:
    normalized = normalize_text(file_name)
    normalized = normalized.replace("\\", "/")
    normalized = normalized.split("/")[-1]
    normalized = re.sub(r"[\x00-\x1f\x7f]", "", normalized)
    normalized = normalized.strip(" .")

    if not normalized:
        raise HTTPException(
            status_code=400,
            detail="ファイル名を確認できませんでした。",
        )

    return normalized


def get_allowed_extensions(data_source: dict) -> list[str]:
    source_extensions = data_source.get("file_extensions", [])

    if not isinstance(source_extensions, list):
        return []

    extensions: list[str] = []

    for item in source_extensions:
        if isinstance(item, str):
            extension = normalize_extension(item)
        elif isinstance(item, dict):
            extension = normalize_extension(
                item.get("extension")
                or item.get("value")
            )
        else:
            extension = ""

        if extension and extension not in extensions:
            extensions.append(extension)

    return sorted(extensions)


def validate_file_extension(
    file_name: str,
    data_source: dict,
) -> str:
    extension = get_file_extension(file_name)

    if not extension:
        raise HTTPException(
            status_code=400,
            detail="拡張子のないファイルは取り込めません。",
        )

    allowed_extensions = get_allowed_extensions(data_source)

    if not allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="データソースに対象拡張子が設定されていません。",
        )

    if extension not in allowed_extensions:
        allowed_text = ", ".join(
            f".{item}"
            for item in allowed_extensions
        )

        raise HTTPException(
            status_code=400,
            detail=(
                f".{extension}は対象外の拡張子です。"
                f" 使用可能: {allowed_text}"
            ),
        )

    return extension

def build_gcs_path(
    data_source_id: str,
    file_id: str,
    file_name: str,
) -> str:
    return (
        f"data-sources/{data_source_id}/"
        f"imports/file-uploads/{file_id}/{file_name}"
    )


def upload_to_storage(
    upload_file: UploadFile,
    gcs_path: str,
) -> None:
    blob = get_storage_bucket().blob(gcs_path)

    try:
        upload_file.file.seek(0)
        blob.upload_from_file(
            upload_file.file,
            content_type=(
                upload_file.content_type
                or "application/octet-stream"
            ),
        )
    except Exception as error:
        print(
            "Cloud Storage upload error: "
            f"{type(error).__name__}: {error}"
        )
        raise HTTPException(
            status_code=500,
            detail="Cloud Storageへのファイル保存に失敗しました。",
        )


def delete_from_storage(gcs_path: str) -> None:
    if not gcs_path:
        return

    try:
        blob = get_storage_bucket().blob(gcs_path)
        if blob.exists():
            blob.delete()
    except Exception as error:
        print(
            "Cloud Storage delete error: "
            f"{type(error).__name__}: {error}"
        )


def find_same_name_file(
    data_source_id: str,
    file_name: str,
):
    documents = (
        get_data_import_collection(data_source_id)
        .where(
            "file_name_normalized",
            "==",
            file_name.lower(),
        )
        .where(
            "deleted",
            "==",
            False,
        )
        .stream()
    )

    for document in documents:
        data = document.to_dict() or {}
        if normalize_text(
            data.get("import_type")
        ).lower() == "file_upload":
            return document

    return None


def save_file_document(
    *,
    data_source: dict,
    file_id: str,
    file_name: str,
    extension: str,
    upload_file: UploadFile,
    gcs_path: str,
    user: dict,
    is_update: bool,
) -> None:
    data = {
        "item_id": file_id,
        "file_id": file_id,
        "data_source_id": data_source["data_source_id"],
        "data_source_name": data_source.get(
            "data_source_name",
            "",
        ),
        "tenant_id": data_source.get("tenant_id", ""),
        "import_type": "file_upload",
        "item_type": "file",
        "level": 0,
        "parent_id": None,
        "file_name": file_name,
        "file_name_normalized": file_name.lower(),
        "display_name": file_name,
        "title": file_name,
        "extension": extension,
        "content_type": (
            upload_file.content_type
            or "application/octet-stream"
        ),
        "size_bytes": upload_file.size,
        "bucket_name": BUCKET_NAME,
        "gcs_path": gcs_path,
        "gcs_uri": f"gs://{BUCKET_NAME}/{gcs_path}",
        "status": "uploaded",
        "deleted": False,
        "analysis_status": firestore.DELETE_FIELD,
        "analysis_batch_id": firestore.DELETE_FIELD,
        "analysis_task_name": firestore.DELETE_FIELD,
        "analysis_error": firestore.DELETE_FIELD,
        "analysis_error_message": firestore.DELETE_FIELD,
        "analysis_started_at": firestore.DELETE_FIELD,
        "analysis_completed_at": firestore.DELETE_FIELD,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "updated_by": user["email"],
    }

    if not is_update:
        data["created_at"] = firestore.SERVER_TIMESTAMP
        data["created_by"] = user["email"]

    (
        get_data_import_collection(
            data_source["data_source_id"]
        )
        .document(file_id)
        .set(data, merge=True)
    )


def execute_file_upload(
    *,
    data_source_id: str,
    upload_file: UploadFile,
    overwrite: bool,
    user: dict,
) -> dict:
    data_source = get_data_source(data_source_id)

    if not data_source.get("enabled", True):
        raise HTTPException(
            status_code=400,
            detail="無効なデータソースです。",
        )

    file_name = sanitize_file_name(
        upload_file.filename or ""
    )
    extension = validate_file_extension(
        file_name,
        data_source,
    )
    existing_document = find_same_name_file(
        data_source["data_source_id"],
        file_name,
    )

    if existing_document and not overwrite:
        existing_data = existing_document.to_dict() or {}
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FILE_ALREADY_EXISTS",
                "message": "同名ファイルが既に登録されています。",
                "existing_file_id": (
                    existing_data.get("file_id")
                    or existing_document.id
                ),
            },
        )

    if existing_document:
        existing_data = existing_document.to_dict() or {}
        file_id = (
            existing_data.get("file_id")
            or existing_document.id
        )
        old_gcs_path = normalize_text(
            existing_data.get("gcs_path")
        )
        is_update = True
    else:
        file_id = uuid.uuid4().hex
        old_gcs_path = ""
        is_update = False

    gcs_path = build_gcs_path(
        data_source["data_source_id"],
        file_id,
        file_name,
    )

    upload_to_storage(upload_file, gcs_path)

    try:
        save_file_document(
            data_source=data_source,
            file_id=file_id,
            file_name=file_name,
            extension=extension,
            upload_file=upload_file,
            gcs_path=gcs_path,
            user=user,
            is_update=is_update,
        )
    except Exception as error:
        delete_from_storage(gcs_path)
        print(
            "Firestore registration error: "
            f"{type(error).__name__}: {error}"
        )
        raise HTTPException(
            status_code=500,
            detail="ファイル管理情報の登録に失敗しました。",
        )

    if (
        is_update
        and old_gcs_path
        and old_gcs_path != gcs_path
    ):
        delete_from_storage(old_gcs_path)

    return {
        "message": (
            "ファイルを上書きしました。"
            if is_update
            else "ファイルを取り込みました。"
        ),
        "data_source_id": data_source["data_source_id"],
        "data_source_name": data_source.get(
            "data_source_name",
            "",
        ),
        "file_id": file_id,
        "item_id": file_id,
        "file_name": file_name,
        "extension": extension,
        "bucket_name": BUCKET_NAME,
        "gcs_path": gcs_path,
        "gcs_uri": f"gs://{BUCKET_NAME}/{gcs_path}",
        "status": "updated" if is_update else "created",
    }
