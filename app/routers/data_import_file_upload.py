import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from firebase_admin import firestore
from app.core.firebase import get_firestore_client
from app.routers.data_import_common import (
    BUCKET_NAME,
    UPLOADED_FILE_COLLECTION,
    get_storage_bucket,
    normalize_extension,
    normalize_text,
)

FILE_TYPE_COLLECTION = "file_types"


def get_file_extension(file_name: str) -> str:
    return normalize_extension(Path(file_name).suffix)


def sanitize_file_name(file_name: str) -> str:
    normalized = normalize_text(file_name)
    normalized = normalized.replace("\\", "/")
    normalized = normalized.split("/")[-1]
    normalized = re.sub(
        r"[\x00-\x1f\x7f]",
        "",
        normalized,
    )
    normalized = normalized.strip(" .")

    if not normalized:
        raise HTTPException(
            status_code=400,
            detail="ファイル名を確認できませんでした。",
        )

    return normalized


def is_enabled_file_type(data: dict) -> bool:
    if data.get("deleted", False):
        return False

    if "enabled" in data:
        return bool(data.get("enabled"))

    status = normalize_text(
        data.get("status", "active")
    ).lower()

    return status not in {
        "disabled",
        "inactive",
        "invalid",
        "無効",
    }


def get_allowed_extensions() -> list[str]:
    documents = (
        get_firestore_client()
        .collection(FILE_TYPE_COLLECTION)
        .stream()
    )

    extensions: list[str] = []

    for document in documents:
        data = document.to_dict() or {}

        if not is_enabled_file_type(data):
            continue

        extension = normalize_extension(
            data.get("extension")
            or data.get("file_extension")
            or data.get("value")
            or document.id
        )

        if extension and extension not in extensions:
            extensions.append(extension)

    return sorted(extensions)


def validate_file_extension(file_name: str) -> str:
    extension = get_file_extension(file_name)

    if not extension:
        raise HTTPException(
            status_code=400,
            detail="拡張子のないファイルは取り込めません。",
        )

    allowed_extensions = get_allowed_extensions()

    if not allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="有効な拡張子が登録されていません。",
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
    file_id: str,
    file_name: str,
) -> str:
    return f"file-uploads/{file_id}/{file_name}"


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
            detail=(
                "Cloud Storageへの"
                "ファイル保存に失敗しました。"
            ),
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


def find_same_name_file(file_name: str):
    documents = (
        get_firestore_client()
        .collection(UPLOADED_FILE_COLLECTION)
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
        .limit(1)
        .stream()
    )

    return next(documents, None)


def save_file_document(
    *,
    file_id: str,
    file_name: str,
    extension: str,
    upload_file: UploadFile,
    gcs_path: str,
    user: dict,
    is_update: bool,
) -> None:
    data = {
        "file_id": file_id,
        "import_type": "file_upload",
        "file_name": file_name,
        "file_name_normalized": file_name.lower(),
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
        "updated_at": firestore.SERVER_TIMESTAMP,
        "updated_by": user["email"],
    }

    if not is_update:
        data["created_at"] = firestore.SERVER_TIMESTAMP
        data["created_by"] = user["email"]

    (
        get_firestore_client()
        .collection(UPLOADED_FILE_COLLECTION)
        .document(file_id)
        .set(data, merge=True)
    )


def execute_file_upload(
    *,
    upload_file: UploadFile,
    overwrite: bool,
    user: dict,
) -> dict:
    file_name = sanitize_file_name(
        upload_file.filename or ""
    )

    extension = validate_file_extension(file_name)
    existing_document = find_same_name_file(file_name)

    if existing_document and not overwrite:
        existing_data = existing_document.to_dict() or {}

        raise HTTPException(
            status_code=409,
            detail={
                "code": "FILE_ALREADY_EXISTS",
                "message": (
                    "同名ファイルが既に"
                    "登録されています。"
                ),
                "existing_file_id": existing_data.get(
                    "file_id",
                    existing_document.id,
                ),
            },
        )

    if existing_document:
        existing_data = existing_document.to_dict() or {}
        file_id = (
            existing_data.get("file_id")
            or existing_document.id
        )
        old_gcs_path = existing_data.get("gcs_path", "")
        is_update = True

    else:
        file_id = uuid.uuid4().hex
        old_gcs_path = ""
        is_update = False

    gcs_path = build_gcs_path(
        file_id,
        file_name,
    )

    upload_to_storage(
        upload_file,
        gcs_path,
    )

    try:
        save_file_document(
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
            detail=(
                "ファイル管理情報の"
                "登録に失敗しました。"
            ),
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
        "file_id": file_id,
        "file_name": file_name,
        "extension": extension,
        "bucket_name": BUCKET_NAME,
        "gcs_path": gcs_path,
        "gcs_uri": f"gs://{BUCKET_NAME}/{gcs_path}",
        "status": (
            "updated"
            if is_update
            else "created"
        ),
    }
