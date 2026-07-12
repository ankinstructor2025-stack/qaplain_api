import os
from datetime import datetime, timezone
from typing import List

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
)
from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


router = APIRouter(
    prefix="/file-types",
    tags=["file-types"],
)


FILE_TYPE_COLLECTION = (
    "supported_file_types"
)


class FileTypeRequest(BaseModel):

    extension: str = Field(
        min_length=1,
        max_length=20,
    )

    display_name: str = Field(
        min_length=1,
        max_length=100,
    )

    mime_types: List[str] = Field(
        min_length=1,
    )

    parser_type: str = Field(
        min_length=1,
        max_length=50,
    )

    max_file_size_mb: int = Field(
        ge=1,
        le=10000,
    )

    enabled: bool = True

    sort_order: int = Field(
        default=10,
        ge=0,
        le=9999,
    )

    @field_validator(
        "extension"
    )
    @classmethod
    def validate_extension(
        cls,
        value: str,
    ) -> str:

        normalized = normalize_extension(
            value
        )

        if not normalized:

            raise ValueError(
                "拡張子を入力してください。"
            )

        if not normalized.isalnum():

            raise ValueError(
                "拡張子は半角英数字で"
                "入力してください。"
            )

        return normalized

    @field_validator(
        "display_name",
        "parser_type",
    )
    @classmethod
    def strip_text(
        cls,
        value: str,
    ) -> str:

        value = value.strip()

        if not value:

            raise ValueError(
                "必須項目を入力してください。"
            )

        return value

    @field_validator(
        "mime_types"
    )
    @classmethod
    def normalize_mime_types(
        cls,
        values: List[str],
    ) -> List[str]:

        normalized_values = []

        for value in values:

            normalized = str(
                value
            ).strip().lower()

            if (
                normalized
                and normalized
                not in normalized_values
            ):

                normalized_values.append(
                    normalized
                )

        if not normalized_values:

            raise ValueError(
                "MIMEタイプを入力してください。"
            )

        return normalized_values


def now_iso() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


def normalize_email(
    email: str,
) -> str:

    return str(
        email or ""
    ).strip().lower()


def normalize_extension(
    extension: str,
) -> str:

    return str(
        extension or ""
    ).strip().lower().lstrip(".")


def authenticate_system_administrator(
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
                "メールアドレスを"
                "取得できませんでした。"
            ),
        )

    system_administrator = normalize_email(
        os.getenv(
            "SYSTEM_ADMINISTRATOR",
            "",
        )
    )

    if (
        not system_administrator
        or email != system_administrator
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "システム管理者権限がありません。"
            ),
        )

    return {
        **decoded_token,
        "email": email,
    }


def get_file_type_document(
    extension: str,
):

    normalized_extension = (
        normalize_extension(
            extension
        )
    )

    db = get_firestore_client()

    document_reference = (
        db.collection(
            FILE_TYPE_COLLECTION
        )
        .document(
            normalized_extension
        )
    )

    document = (
        document_reference.get()
    )

    if not document.exists:

        raise HTTPException(
            status_code=404,
            detail=(
                "拡張子設定が"
                "見つかりません。"
            ),
        )

    return (
        normalized_extension,
        document_reference,
        document,
    )


def document_to_dict(
    document,
) -> dict:

    data = document.to_dict() or {}

    return {
        "extension":
            data.get(
                "extension",
                document.id,
            ),

        "display_name":
            data.get(
                "display_name",
                "",
            ),

        "mime_types":
            data.get(
                "mime_types",
                [],
            ),

        "parser_type":
            data.get(
                "parser_type",
                "",
            ),

        "max_file_size_mb":
            data.get(
                "max_file_size_mb",
                50,
            ),

        "enabled":
            data.get(
                "enabled",
                True,
            ),

        "sort_order":
            data.get(
                "sort_order",
                10,
            ),

        "created_at":
            data.get(
                "created_at"
            ),

        "created_by":
            data.get(
                "created_by"
            ),

        "updated_at":
            data.get(
                "updated_at"
            ),

        "updated_by":
            data.get(
                "updated_by"
            ),
    }


@router.get("")
def get_file_types(
    authorization: str = Header(...),
):

    authenticate_system_administrator(
        authorization
    )

    db = get_firestore_client()

    documents = (
        db.collection(
            FILE_TYPE_COLLECTION
        )
        .stream()
    )

    file_types = [
        document_to_dict(
            document
        )
        for document in documents
    ]

    file_types.sort(
        key=lambda item: (
            item.get(
                "sort_order",
                9999,
            ),
            item.get(
                "extension",
                "",
            ),
        )
    )

    return {
        "file_types": file_types
    }


@router.get("/{extension}")
def get_file_type(
    extension: str,
    authorization: str = Header(...),
):

    authenticate_system_administrator(
        authorization
    )

    (
        _,
        _,
        document,
    ) = get_file_type_document(
        extension
    )

    return document_to_dict(
        document
    )


@router.post(
    "",
    status_code=201,
)
def create_file_type(
    request: FileTypeRequest,
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_system_administrator(
            authorization
        )
    )

    extension = normalize_extension(
        request.extension
    )

    db = get_firestore_client()

    document_reference = (
        db.collection(
            FILE_TYPE_COLLECTION
        )
        .document(
            extension
        )
    )

    document = (
        document_reference.get()
    )

    if document.exists:

        raise HTTPException(
            status_code=409,
            detail=(
                f".{extension}は"
                "既に登録されています。"
            ),
        )

    now = now_iso()

    email = authenticated_user.get(
        "email",
        "",
    )

    data = {
        "extension":
            extension,

        "display_name":
            request.display_name,

        "mime_types":
            request.mime_types,

        "parser_type":
            request.parser_type,

        "max_file_size_mb":
            request.max_file_size_mb,

        "enabled":
            request.enabled,

        "sort_order":
            request.sort_order,

        "created_at":
            now,

        "created_by":
            email,

        "updated_at":
            now,

        "updated_by":
            email,
    }

    document_reference.set(
        data
    )

    return data


@router.put("/{extension}")
def update_file_type(
    extension: str,
    request: FileTypeRequest,
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_system_administrator(
            authorization
        )
    )

    (
        normalized_extension,
        document_reference,
        _,
    ) = get_file_type_document(
        extension
    )

    request_extension = (
        normalize_extension(
            request.extension
        )
    )

    if (
        request_extension
        != normalized_extension
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "登録済みの拡張子は"
                "変更できません。"
            ),
        )

    update_data = {
        "display_name":
            request.display_name,

        "mime_types":
            request.mime_types,

        "parser_type":
            request.parser_type,

        "max_file_size_mb":
            request.max_file_size_mb,

        "enabled":
            request.enabled,

        "sort_order":
            request.sort_order,

        "updated_at":
            now_iso(),

        "updated_by":
            authenticated_user.get(
                "email",
                "",
            ),
    }

    document_reference.update(
        update_data
    )

    return document_to_dict(
        document_reference.get()
    )


@router.delete("/{extension}")
def delete_file_type(
    extension: str,
    authorization: str = Header(...),
):

    authenticate_system_administrator(
        authorization
    )

    (
        normalized_extension,
        document_reference,
        _,
    ) = get_file_type_document(
        extension
    )

    document_reference.delete()

    return {
        "status": "deleted",
        "extension": normalized_extension,
    }