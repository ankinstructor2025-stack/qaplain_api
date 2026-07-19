from typing import Any

from fastapi import HTTPException
from firebase_admin import firestore

from app.core.firebase import get_firestore_client


AUTHENTICATION_METHOD_COLLECTION = "authentication_methods"
FILE_TYPE_COLLECTION = "supported_file_types"
TENANT_COLLECTION = "tenants"


PROCESSING_PATTERNS = {
    "raw",
    "json_list",
    "parent_child",
    "parent_child_grandchild",
    "file_links",
}


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_key(value: Any) -> str:
    return normalize_text(value).lower().replace("-", "_")


def normalize_file_extension(value: Any) -> str:
    return normalize_text(value).lower().lstrip(".")


def validate_processing_pattern(value: Any) -> str:
    processing_pattern = normalize_key(value) or "raw"

    if processing_pattern not in PROCESSING_PATTERNS:
        raise HTTPException(
            status_code=400,
            detail="処理方式が正しくありません。",
        )

    return processing_pattern


PROCESSING_SETTING_FIELDS = (
    "list_array_path",
    "parent_array_path",
    "child_array_path",
    "grandchild_array_path",
    "file_link_array_path",
    "file_link_field_name",
)


def validate_processing_settings(
    request,
) -> None:
    processing_pattern = validate_processing_pattern(
        getattr(request, "processing_pattern", "raw")
    )

    required_fields = {
        "raw": (),
        "json_list": (
            (
                "list_array_path",
                "一覧配列を入力してください。",
            ),
        ),
        "parent_child": (
            (
                "parent_array_path",
                "親配列を入力してください。",
            ),
            (
                "child_array_path",
                "子配列を入力してください。",
            ),
        ),
        "parent_child_grandchild": (
            (
                "parent_array_path",
                "親配列を入力してください。",
            ),
            (
                "child_array_path",
                "子配列を入力してください。",
            ),
            (
                "grandchild_array_path",
                "孫配列を入力してください。",
            ),
        ),
        "file_links": (
            (
                "file_link_array_path",
                "一覧配列を入力してください。",
            ),
            (
                "file_link_field_name",
                "ファイルURL項目を入力してください。",
            ),
        ),
    }

    for field_name, message in required_fields[
        processing_pattern
    ]:
        if not normalize_text(
            getattr(request, field_name, "")
        ):
            raise HTTPException(
                status_code=400,
                detail=message,
            )


def set_processing_settings_data(
    data: dict,
    request,
) -> None:
    processing_pattern = validate_processing_pattern(
        getattr(request, "processing_pattern", "raw")
    )

    active_fields = {
        "raw": (),
        "json_list": (
            "list_array_path",
        ),
        "parent_child": (
            "parent_array_path",
            "child_array_path",
        ),
        "parent_child_grandchild": (
            "parent_array_path",
            "child_array_path",
            "grandchild_array_path",
        ),
        "file_links": (
            "file_link_array_path",
            "file_link_field_name",
        ),
    }[processing_pattern]

    for field_name in PROCESSING_SETTING_FIELDS:
        if field_name in active_fields:
            data[field_name] = normalize_text(
                getattr(request, field_name, "")
            )
        else:
            data[field_name] = firestore.DELETE_FIELD


def validate_tenant_id(
    tenant_id: Any,
) -> str:
    normalized_tenant_id = normalize_text(
        tenant_id
    )

    if not normalized_tenant_id:
        raise HTTPException(
            status_code=400,
            detail="テナントを選択してください。",
        )

    document = (
        get_firestore_client()
        .collection(TENANT_COLLECTION)
        .document(normalized_tenant_id)
        .get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=400,
            detail="選択したテナントが見つかりません。",
        )

    return normalized_tenant_id


def get_authentication_method(method_key: str) -> dict:
    normalized_method_key = normalize_key(method_key)

    if not normalized_method_key:
        raise HTTPException(
            status_code=400,
            detail="認証方式を選択してください。",
        )

    document = (
        get_firestore_client()
        .collection(AUTHENTICATION_METHOD_COLLECTION)
        .document(normalized_method_key)
        .get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=400,
            detail="認証方式が登録されていません。",
        )

    data = document.to_dict() or {}

    if data.get("enabled", True) is False:
        raise HTTPException(
            status_code=400,
            detail="無効な認証方式です。",
        )

    return {
        "method_key": normalized_method_key,
        "display_name": data.get("display_name", ""),
    }


def validate_file_extensions(
    file_extensions: list[str],
) -> list[str]:
    normalized_extensions = list(
        dict.fromkeys(
            normalize_file_extension(extension)
            for extension in file_extensions
            if normalize_file_extension(extension)
        )
    )

    if not normalized_extensions:
        raise HTTPException(
            status_code=400,
            detail="対象拡張子を1つ以上選択してください。",
        )

    db = get_firestore_client()

    for extension in normalized_extensions:
        document = (
            db.collection(FILE_TYPE_COLLECTION)
            .document(extension)
            .get()
        )

        if not document.exists:
            raise HTTPException(
                status_code=400,
                detail=f".{extension}は拡張子管理に登録されていません。",
            )

        data = document.to_dict() or {}

        if data.get("enabled", True) is False:
            raise HTTPException(
                status_code=400,
                detail=f".{extension}は無効な拡張子です。",
            )

    return normalized_extensions


def create_common_data(
    request,
    method_key: str,
) -> dict:
    data = {
        "tenant_id": validate_tenant_id(
            request.tenant_id
        ),
        "data_source_name": normalize_text(
            request.data_source_name
        ),
        "source_type": normalize_key(
            request.source_type
        ),
        "processing_pattern": validate_processing_pattern(
            getattr(request, "processing_pattern", "raw")
        ),
        "enabled": request.enabled,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }

    normalized_method_key = normalize_key(method_key)

    if normalized_method_key:
        data["authentication_method_key"] = (
            normalized_method_key
        )

    return data


def delete_connection_fields(data: dict) -> None:
    data.update({
        "endpoint_url": firestore.DELETE_FIELD,
        "http_method": firestore.DELETE_FIELD,
        "file_extensions": firestore.DELETE_FIELD,
        "username": firestore.DELETE_FIELD,
        "password": firestore.DELETE_FIELD,
        "client_id": firestore.DELETE_FIELD,
        "client_secret": firestore.DELETE_FIELD,
        "token_url": firestore.DELETE_FIELD,
        "scope": firestore.DELETE_FIELD,
        "retrieval_type": firestore.DELETE_FIELD,
        "data_format": firestore.DELETE_FIELD,
    })


def set_external_connection_data(
    data: dict,
    request,
) -> None:
    source_type = normalize_key(
        request.source_type
    )

    data["endpoint_url"] = normalize_text(
        request.endpoint_url
    )
    data["http_method"] = (
        normalize_text(request.http_method).upper()
        if source_type == "api"
        else "GET"
    ) or "GET"
