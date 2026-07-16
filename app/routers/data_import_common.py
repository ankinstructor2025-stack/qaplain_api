import os
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from google.cloud import storage

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


BUCKET_NAME = os.getenv(
    "UPLOAD_BUCKET",
    "qaplain",
)

DATA_SOURCE_COLLECTION = "data_sources"
PARAMETER_COLLECTION = "parameters"
UPLOADED_FILE_COLLECTION = "uploaded_files"
DATA_IMPORT_COLLECTION = "data_import_items"


def normalize_text(
    value: Any,
) -> str:
    return str(
        value or ""
    ).strip()


def normalize_email(
    value: Any,
) -> str:
    return normalize_text(
        value
    ).lower()


def normalize_key(
    value: Any,
) -> str:
    return (
        normalize_text(
            value
        )
        .lower()
        .replace(
            "-",
            "_",
        )
    )


def normalize_extension(
    value: Any,
) -> str:
    return (
        normalize_text(
            value
        )
        .lower()
        .lstrip(
            "."
        )
    )


def authenticate_user(
    authorization: str,
) -> dict:
    if not authorization.startswith(
        "Bearer "
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header",
        )

    id_token = authorization.replace(
        "Bearer ",
        "",
        1,
    ).strip()

    if not id_token:
        raise HTTPException(
            status_code=401,
            detail="認証情報がありません。",
        )

    try:
        decoded_token = verify_id_token(
            id_token
        )

    except Exception as error:
        print(
            "verify_id_token error: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=401,
            detail="認証情報を確認できませんでした。",
        )

    email = normalize_email(
        decoded_token.get(
            "email",
            "",
        )
    )

    if not email:
        raise HTTPException(
            status_code=401,
            detail="メールアドレスを取得できませんでした。",
        )

    return {
        **decoded_token,
        "email": email,
    }


def serialize_datetime(
    value: Any,
) -> str | None:
    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat()

    if hasattr(
        value,
        "isoformat",
    ):
        try:
            return value.isoformat()

        except Exception:
            pass

    return str(
        value
    )


def serialize_value(
    value: Any,
) -> Any:
    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat()

    if hasattr(
        value,
        "isoformat",
    ):
        try:
            return value.isoformat()

        except Exception:
            pass

    if isinstance(
        value,
        list,
    ):
        return [
            serialize_value(
                item
            )
            for item in value
        ]

    if isinstance(
        value,
        dict,
    ):
        return {
            key:
                serialize_value(
                    item
                )
            for key, item
            in value.items()
        }

    return value


def load_parameters(
    document_reference,
) -> list[dict]:
    documents = (
        document_reference
        .collection(
            PARAMETER_COLLECTION
        )
        .order_by(
            "display_order"
        )
        .stream()
    )

    parameters = []

    for document in documents:
        data = document.to_dict() or {}

        parameter_name = normalize_text(
            data.get(
                "parameter_name",
                "",
            )
        )

        if not parameter_name:
            continue

        parameters.append({
            "parameter_id":
                document.id,

            "parameter_name":
                parameter_name,

            "parameter_value":
                data.get(
                    "parameter_value",
                    "",
                ),

            "display_order":
                data.get(
                    "display_order",
                    0,
                ),
        })

    return parameters


def get_data_source(
    data_source_id: str,
) -> dict:
    normalized_id = normalize_text(
        data_source_id
    )

    if not normalized_id:
        raise HTTPException(
            status_code=400,
            detail="データソースIDが指定されていません。",
        )

    document_reference = (
        get_firestore_client()
        .collection(
            DATA_SOURCE_COLLECTION
        )
        .document(
            normalized_id
        )
    )

    document = document_reference.get()

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail="データソースが見つかりません。",
        )

    data = document.to_dict() or {}

    return {
        "data_source_id":
            document.id,

        "data_source_name":
            data.get(
                "data_source_name",
                "",
            ),

        "source_type":
            data.get(
                "source_type",
                "",
            ),

        "authentication_method_key":
            normalize_key(
                data.get(
                    "authentication_method_key",
                    "",
                )
            ),

        "endpoint_url":
            normalize_text(
                data.get(
                    "endpoint_url",
                    "",
                )
            ),

        "http_method":
            normalize_text(
                data.get(
                    "http_method",
                    "GET",
                )
            ).upper(),

        "file_extensions":
            data.get(
                "file_extensions",
                [],
            ),

        "username":
            data.get(
                "username",
                "",
            ),

        "password":
            data.get(
                "password",
                "",
            ),

        "client_id":
            data.get(
                "client_id",
                "",
            ),

        "client_secret":
            data.get(
                "client_secret",
                "",
            ),

        "token_url":
            data.get(
                "token_url",
                "",
            ),

        "scope":
            data.get(
                "scope",
                "",
            ),

        "parameters":
            load_parameters(
                document_reference
            ),

        "enabled":
            data.get(
                "enabled",
                True,
            ),
    }


def validate_common_data_source(
    data_source: dict,
    expected_method_key: str,
) -> None:
    if not data_source.get(
        "enabled",
        True,
    ):
        raise HTTPException(
            status_code=400,
            detail="無効なデータソースです。",
        )

    actual_method_key = normalize_key(
        data_source.get(
            "authentication_method_key",
            "",
        )
    )

    if actual_method_key != expected_method_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "選択したデータソースの認証方式が"
                f"{expected_method_key}ではありません。"
            ),
        )


def get_storage_bucket():
    return storage.Client().bucket(
        BUCKET_NAME
    )


def delete_from_storage(
    gcs_path: str,
) -> None:
    if not gcs_path:
        return

    try:
        blob = get_storage_bucket().blob(
            gcs_path
        )

        if blob.exists():
            blob.delete()

    except Exception as error:
        print(
            "Cloud Storage delete error: "
            f"{type(error).__name__}: "
            f"{error}"
        )
