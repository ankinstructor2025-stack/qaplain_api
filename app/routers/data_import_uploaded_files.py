from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Query,
)

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


router = APIRouter(
    prefix="/data-import/uploaded-files",
    tags=["data-import-uploaded-files"],
)


UPLOADED_FILE_COLLECTION = (
    "uploaded_files"
)


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


def authenticate_user(
    authorization: str,
) -> dict:
    if not authorization.startswith(
        "Bearer "
    ):
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid Authorization header"
            ),
        )

    id_token = authorization.replace(
        "Bearer ",
        "",
        1,
    ).strip()

    if not id_token:
        raise HTTPException(
            status_code=401,
            detail=(
                "認証情報がありません。"
            ),
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
            detail=(
                "認証情報を確認できませんでした。"
            ),
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
            detail=(
                "メールアドレスを取得できませんでした。"
            ),
        )

    return {
        **decoded_token,
        "email":
            email,
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


def document_to_dict(
    document,
) -> dict:
    data = document.to_dict() or {}

    return {
        "file_id":
            data.get(
                "file_id",
                document.id,
            ),

        "data_source_id":
            data.get(
                "data_source_id",
                "",
            ),

        "data_source_name":
            data.get(
                "data_source_name",
                "",
            ),

        "file_name":
            data.get(
                "file_name",
                "",
            ),

        "extension":
            data.get(
                "extension",
                "",
            ),

        "content_type":
            data.get(
                "content_type",
                "",
            ),

        "size_bytes":
            data.get(
                "size_bytes"
            ),

        "bucket_name":
            data.get(
                "bucket_name",
                "",
            ),

        "gcs_path":
            data.get(
                "gcs_path",
                "",
            ),

        "status":
            data.get(
                "status",
                "uploaded",
            ),

        "created_at":
            serialize_datetime(
                data.get(
                    "created_at"
                )
            ),

        "created_by":
            data.get(
                "created_by",
                "",
            ),

        "updated_at":
            serialize_datetime(
                data.get(
                    "updated_at"
                )
            ),

        "updated_by":
            data.get(
                "updated_by",
                "",
            ),
    }


@router.get("")
def get_uploaded_files(
    data_source_id: str = Query(
        ...,
        min_length=1,
    ),
    authorization: str = Header(...),
):
    authenticate_user(
        authorization
    )

    normalized_data_source_id = (
        normalize_text(
            data_source_id
        )
    )

    db = get_firestore_client()

    documents = (
        db.collection(
            UPLOADED_FILE_COLLECTION
        )
        .where(
            "data_source_id",
            "==",
            normalized_data_source_id,
        )
        .stream()
    )

    uploaded_files = []

    for document in documents:
        data = document.to_dict() or {}

        if data.get(
            "deleted",
            False,
        ):
            continue

        uploaded_files.append(
            document_to_dict(
                document
            )
        )

    uploaded_files.sort(
        key=lambda item:
        item.get(
            "updated_at"
        )
        or item.get(
            "created_at"
        )
        or "",
        reverse=True,
    )

    return {
        "uploaded_files":
            uploaded_files,

        "count":
            len(
                uploaded_files
            ),
    }
