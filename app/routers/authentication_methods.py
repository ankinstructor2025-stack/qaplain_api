import os
import re
from datetime import datetime, timezone
from typing import Optional

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
    prefix="/authentication-methods",
    tags=["authentication-methods"],
)


AUTHENTICATION_METHOD_COLLECTION = (
    "authentication_methods"
)

DATA_SOURCE_CONFIG_COLLECTION = (
    "data_source_configs"
)


class AuthenticationMethodRequest(
    BaseModel
):

    method_key: str = Field(
        min_length=1,
        max_length=50,
    )

    display_name: str = Field(
        min_length=1,
        max_length=100,
    )

    description: Optional[str] = Field(
        default="",
        max_length=500,
    )

    enabled: bool = True

    @field_validator(
        "method_key"
    )
    @classmethod
    def validate_method_key(
        cls,
        value: str,
    ) -> str:

        normalized = normalize_method_key(
            value
        )

        if not normalized:

            raise ValueError(
                "認証方式キーを入力してください。"
            )

        if not re.fullmatch(
            r"[a-z0-9_]+",
            normalized,
        ):

            raise ValueError(
                "認証方式キーは半角英数字と"
                "アンダースコアで入力してください。"
            )

        return normalized

    @field_validator(
        "display_name"
    )
    @classmethod
    def validate_display_name(
        cls,
        value: str,
    ) -> str:

        normalized = str(
            value or ""
        ).strip()

        if not normalized:

            raise ValueError(
                "表示名を入力してください。"
            )

        return normalized

    @field_validator(
        "description"
    )
    @classmethod
    def normalize_description(
        cls,
        value: Optional[str],
    ) -> str:

        return str(
            value or ""
        ).strip()


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


def normalize_method_key(
    method_key: str,
) -> str:

    normalized = str(
        method_key or ""
    ).strip().lower()

    normalized = re.sub(
        r"[\s\-]+",
        "_",
        normalized,
    )

    return normalized


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


def get_authentication_method_document(
    method_key: str,
):

    normalized_method_key = (
        normalize_method_key(
            method_key
        )
    )

    db = get_firestore_client()

    document_reference = (
        db.collection(
            AUTHENTICATION_METHOD_COLLECTION
        )
        .document(
            normalized_method_key
        )
    )

    document = (
        document_reference.get()
    )

    if not document.exists:

        raise HTTPException(
            status_code=404,
            detail=(
                "認証方式が"
                "見つかりません。"
            ),
        )

    return (
        normalized_method_key,
        document_reference,
        document,
    )


def document_to_dict(
    document,
) -> dict:

    data = document.to_dict() or {}

    return {
        "method_key":
            data.get(
                "method_key",
                document.id,
            ),

        "display_name":
            data.get(
                "display_name",
                "",
            ),

        "description":
            data.get(
                "description",
                "",
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


def check_authentication_method_in_use(
    method_key: str,
) -> None:

    db = get_firestore_client()

    documents = (
        db.collection(
            DATA_SOURCE_CONFIG_COLLECTION
        )
        .where(
            "authentication_method",
            "==",
            method_key,
        )
        .limit(1)
        .stream()
    )

    document = next(
        documents,
        None,
    )

    if document:

        data = document.to_dict() or {}

        source_name = (
            data.get(
                "source_name"
            )
            or data.get(
                "config_name"
            )
            or ""
        )

        if source_name:

            detail = (
                f"この認証方式は"
                f"「{source_name}」から"
                "参照されているため削除できません。"
            )

        else:

            detail = (
                "この認証方式は"
                "データ取得設定から"
                "参照されているため削除できません。"
            )

        raise HTTPException(
            status_code=409,
            detail=detail,
        )


@router.get("")
def get_authentication_methods(
    authorization: str = Header(...),
):

    authenticate_system_administrator(
        authorization
    )

    db = get_firestore_client()

    documents = (
        db.collection(
            AUTHENTICATION_METHOD_COLLECTION
        )
        .stream()
    )

    authentication_methods = [
        document_to_dict(
            document
        )
        for document in documents
    ]

    authentication_methods.sort(
        key=lambda item: (
            not item.get(
                "enabled",
                True,
            ),
            item.get(
                "display_name",
                "",
            ),
            item.get(
                "method_key",
                "",
            ),
        )
    )

    return {
        "authentication_methods":
            authentication_methods
    }


def authenticate_logged_in_user(
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


@router.get("/available")
def get_available_authentication_methods(
    authorization: str = Header(...),
):

    authenticate_logged_in_user(
        authorization
    )

    db = get_firestore_client()

    documents = (
        db.collection(
            AUTHENTICATION_METHOD_COLLECTION
        )
        .where(
            "enabled",
            "==",
            True,
        )
        .stream()
    )

    authentication_methods = [
        document_to_dict(
            document
        )
        for document in documents
    ]

    authentication_methods.sort(
        key=lambda item:
        item.get(
            "display_name",
            "",
        )
    )

    return {
        "authentication_methods":
            authentication_methods
    }

@router.get("/{method_key}")
def get_authentication_method(
    method_key: str,
    authorization: str = Header(...),
):

    authenticate_system_administrator(
        authorization
    )

    (
        _,
        _,
        document,
    ) = get_authentication_method_document(
        method_key
    )

    return document_to_dict(
        document
    )


@router.post(
    "",
    status_code=201,
)
def create_authentication_method(
    request: AuthenticationMethodRequest,
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_system_administrator(
            authorization
        )
    )

    method_key = normalize_method_key(
        request.method_key
    )

    db = get_firestore_client()

    document_reference = (
        db.collection(
            AUTHENTICATION_METHOD_COLLECTION
        )
        .document(
            method_key
        )
    )

    document = (
        document_reference.get()
    )

    if document.exists:

        raise HTTPException(
            status_code=409,
            detail=(
                f"認証方式「{method_key}」は"
                "既に登録されています。"
            ),
        )

    now = now_iso()

    email = authenticated_user.get(
        "email",
        "",
    )

    data = {
        "method_key":
            method_key,

        "display_name":
            request.display_name,

        "description":
            request.description or "",

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


@router.put("/{method_key}")
def update_authentication_method(
    method_key: str,
    request: AuthenticationMethodRequest,
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_system_administrator(
            authorization
        )
    )

    (
        normalized_method_key,
        document_reference,
        _,
    ) = get_authentication_method_document(
        method_key
    )

    request_method_key = (
        normalize_method_key(
            request.method_key
        )
    )

    if (
        request_method_key
        != normalized_method_key
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "登録済みの認証方式キーは"
                "変更できません。"
            ),
        )

    update_data = {
        "display_name":
            request.display_name,

        "description":
            request.description or "",

        "enabled":
            request.enabled,

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


@router.delete("/{method_key}")
def delete_authentication_method(
    method_key: str,
    authorization: str = Header(...),
):

    authenticate_system_administrator(
        authorization
    )

    (
        normalized_method_key,
        document_reference,
        _,
    ) = get_authentication_method_document(
        method_key
    )

    check_authentication_method_in_use(
        normalized_method_key
    )

    document_reference.delete()

    return {
        "status": "deleted",
        "method_key":
            normalized_method_key,
    }