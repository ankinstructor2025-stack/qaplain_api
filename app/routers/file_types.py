import os
import re
from datetime import datetime, timezone

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

    enabled: bool = True

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

        if not re.fullmatch(
            r"[a-z0-9]+",
            normalized,
        ):

            raise ValueError(
                "拡張子は半角英数字で"
                "入力してください。"
            )

        return normalized


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

    return {
        **decoded_token,
        "email": email,
    }


def authenticate_system_administrator(
    authorization: str,
) -> dict:

    decoded_token = authenticate_user(
        authorization
    )

    email = decoded_token.get(
        "email",
        "",
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

    return decoded_token


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

        "enabled":
            data.get(
                "enabled",
                True,
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
        key=lambda item:
        item.get(
            "extension",
            "",
        )
    )

    return {
        "file_types": file_types
    }


@router.get("/available")
def get_available_file_types(
    authorization: str = Header(...),
):

    authenticate_user(
        authorization
    )

    db = get_firestore_client()

    documents = (
        db.collection(
            FILE_TYPE_COLLECTION
        )
        .where(
            "enabled",
            "==",
            True,
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
        key=lambda item:
        item.get(
            "extension",
            "",
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

        "enabled":
            request.enabled,

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

    document_reference.update({
        "enabled":
            request.enabled,

        "updated_at":
            now_iso(),

        "updated_by":
            authenticated_user.get(
                "email",
                "",
            ),
    })

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