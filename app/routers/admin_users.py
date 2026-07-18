import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


router = APIRouter(
    prefix="/admin-users",
    tags=["admin-users"],
)


ADMIN_COLLECTION = "admin_users"
GENERAL_COLLECTION = "general_users"
TENANT_COLLECTION = "tenants"


class AdminUserRequest(BaseModel):
    user_name: str = Field(
        min_length=1,
        max_length=100,
    )

    email: str = Field(
        min_length=1,
        max_length=200,
    )

    tenant_id: str = Field(
        min_length=1,
        max_length=200,
    )

    start_date: str
    end_date: Optional[str] = None


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def normalize_text(
    value,
) -> str:
    return str(
        value or ""
    ).strip()


def normalize_email(
    email: str,
) -> str:
    return normalize_text(
        email
    ).lower()


def normalize_end_date(
    end_date: Optional[str],
) -> Optional[str]:
    if not end_date:
        return None

    return end_date.strip() or None


def validate_date_range(
    start_date: str,
    end_date: Optional[str],
) -> None:
    if not start_date:
        raise HTTPException(
            status_code=400,
            detail="利用開始日を入力してください。",
        )

    if (
        end_date
        and start_date > end_date
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "利用終了日は利用開始日以降"
                "にしてください。"
            ),
        )


def authenticate_system_administrator(
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

    system_administrator = normalize_email(
        os.getenv(
            "SYSTEM_ADMINISTRATOR",
            "",
        )
    )

    if not system_administrator:
        raise HTTPException(
            status_code=500,
            detail=(
                "SYSTEM_ADMINISTRATOR "
                "is not configured"
            ),
        )

    if email != system_administrator:
        raise HTTPException(
            status_code=403,
            detail=(
                "SYSTEM_ADMINISTRATOR "
                "permission is required"
            ),
        )

    return {
        **decoded_token,
        "email":
            email,
    }


def get_document_by_email(
    collection_name: str,
    email: str,
):
    documents = (
        get_firestore_client()
        .collection(
            collection_name
        )
        .where(
            "email",
            "==",
            normalize_email(
                email
            ),
        )
        .limit(1)
        .stream()
    )

    return next(
        documents,
        None,
    )


def get_tenant_document(
    tenant_id: str,
):
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
        .collection(
            TENANT_COLLECTION
        )
        .document(
            normalized_tenant_id
        )
        .get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=400,
            detail="指定されたテナントが見つかりません。",
        )

    return document


def tenant_to_dict(
    document,
) -> dict:
    data = document.to_dict() or {}

    return {
        "tenant_id":
            document.id,
        "tenant_name":
            data.get(
                "tenant_name",
                "",
            ),
        "parent_user":
            data.get(
                "parent_user",
                "",
            ),
        "start_date":
            data.get(
                "start_date"
            ),
        "end_date":
            data.get(
                "end_date"
            ),
    }


def check_duplicate_email(
    email: str,
    exclude_id: Optional[str] = None,
) -> None:
    document = get_document_by_email(
        ADMIN_COLLECTION,
        email,
    )

    if (
        document
        and document.id != exclude_id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "同じメールアドレスが"
                "既に登録されています。"
            ),
        )


def get_tenant_name(
    tenant_id: str,
) -> str:
    if not tenant_id:
        return ""

    document = (
        get_firestore_client()
        .collection(
            TENANT_COLLECTION
        )
        .document(
            tenant_id
        )
        .get()
    )

    if not document.exists:
        return ""

    return (
        document.to_dict()
        or {}
    ).get(
        "tenant_name",
        "",
    )


def document_to_dict(
    document,
) -> dict:
    data = document.to_dict() or {}

    tenant_id = normalize_text(
        data.get(
            "tenant_id",
            "",
        )
    )

    return {
        "id":
            document.id,
        "user_name":
            data.get(
                "user_name",
                "",
            ),
        "email":
            data.get(
                "email",
                "",
            ),
        "tenant_id":
            tenant_id,
        "tenant_name":
            get_tenant_name(
                tenant_id
            ),
        "start_date":
            data.get(
                "start_date"
            ),
        "end_date":
            data.get(
                "end_date"
            ),
        "created_at":
            data.get(
                "created_at"
            ),
        "updated_at":
            data.get(
                "updated_at"
            ),
    }


def sync_general_user(
    db,
    *,
    user_name: str,
    email: str,
    tenant_id: str,
    start_date: str,
    end_date: Optional[str],
    updated_at: str,
) -> None:
    document = get_document_by_email(
        GENERAL_COLLECTION,
        email,
    )

    data = {
        "user_name":
            user_name,
        "email":
            email,
        "tenant_id":
            tenant_id,
        "user_type":
            "ADMIN",
        "parent_user":
            email,
        "start_date":
            start_date,
        "end_date":
            end_date,
        "updated_at":
            updated_at,
    }

    if document:
        document.reference.set(
            data,
            merge=True,
        )
        return

    data["created_at"] = (
        updated_at
    )

    (
        db.collection(
            GENERAL_COLLECTION
        )
        .document()
        .set(
            data
        )
    )


def delete_general_users_for_admin(
    db,
    *,
    email: str,
    tenant_id: str,
) -> None:
    documents = (
        db.collection(
            GENERAL_COLLECTION
        )
        .where(
            "tenant_id",
            "==",
            tenant_id,
        )
        .stream()
    )

    for document in documents:
        data = document.to_dict() or {}

        if (
            normalize_email(
                data.get(
                    "parent_user",
                    "",
                )
            )
            == email
        ):
            document.reference.delete()


@router.get(
    "/available-tenants"
)
def get_available_tenants(
    authorization: str = Header(...),
):
    authenticate_system_administrator(
        authorization
    )

    documents = (
        get_firestore_client()
        .collection(
            TENANT_COLLECTION
        )
        .order_by(
            "tenant_name"
        )
        .stream()
    )

    return {
        "tenants": [
            tenant_to_dict(
                document
            )
            for document in documents
        ]
    }


@router.get("")
def get_admin_users(
    authorization: str = Header(...),
):
    authenticate_system_administrator(
        authorization
    )

    documents = (
        get_firestore_client()
        .collection(
            ADMIN_COLLECTION
        )
        .order_by(
            "user_name"
        )
        .stream()
    )

    return {
        "users": [
            document_to_dict(
                document
            )
            for document in documents
        ]
    }


@router.get(
    "/{admin_user_id}"
)
def get_admin_user(
    admin_user_id: str,
    authorization: str = Header(...),
):
    authenticate_system_administrator(
        authorization
    )

    document = (
        get_firestore_client()
        .collection(
            ADMIN_COLLECTION
        )
        .document(
            admin_user_id
        )
        .get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "管理ユーザーが"
                "見つかりません。"
            ),
        )

    return document_to_dict(
        document
    )


@router.post(
    "",
    status_code=201,
)
def create_admin_user(
    request: AdminUserRequest,
    authorization: str = Header(...),
):
    authenticate_system_administrator(
        authorization
    )

    user_name = request.user_name.strip()
    email = normalize_email(
        request.email
    )
    tenant_id = normalize_text(
        request.tenant_id
    )
    start_date = request.start_date.strip()
    end_date = normalize_end_date(
        request.end_date
    )

    validate_date_range(
        start_date,
        end_date,
    )

    get_tenant_document(
        tenant_id
    )

    check_duplicate_email(
        email
    )

    now = now_iso()

    data = {
        "user_name":
            user_name,
        "email":
            email,
        "tenant_id":
            tenant_id,
        "start_date":
            start_date,
        "end_date":
            end_date,
        "created_at":
            now,
        "updated_at":
            now,
    }

    db = get_firestore_client()

    document_reference = (
        db.collection(
            ADMIN_COLLECTION
        )
        .document()
    )

    document_reference.set(
        data
    )

    sync_general_user(
        db,
        user_name=user_name,
        email=email,
        tenant_id=tenant_id,
        start_date=start_date,
        end_date=end_date,
        updated_at=now,
    )

    return {
        "id":
            document_reference.id,
        **data,
    }


@router.put(
    "/{admin_user_id}"
)
def update_admin_user(
    admin_user_id: str,
    request: AdminUserRequest,
    authorization: str = Header(...),
):
    authenticate_system_administrator(
        authorization
    )

    db = get_firestore_client()

    document_reference = (
        db.collection(
            ADMIN_COLLECTION
        )
        .document(
            admin_user_id
        )
    )

    document = document_reference.get()

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "管理ユーザーが"
                "見つかりません。"
            ),
        )

    current = (
        document.to_dict()
        or {}
    )

    current_email = normalize_email(
        current.get(
            "email",
            "",
        )
    )

    current_start_date = current.get(
        "start_date",
        "",
    )

    if (
        normalize_email(
            request.email
        )
        != current_email
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "メールアドレスは"
                "変更できません。"
            ),
        )

    if (
        request.start_date.strip()
        != current_start_date
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "利用開始日は"
                "変更できません。"
            ),
        )

    tenant_id = normalize_text(
        request.tenant_id
    )

    get_tenant_document(
        tenant_id
    )

    user_name = request.user_name.strip()

    end_date = normalize_end_date(
        request.end_date
    )

    validate_date_range(
        current_start_date,
        end_date,
    )

    now = now_iso()

    document_reference.update({
        "user_name":
            user_name,
        "tenant_id":
            tenant_id,
        "end_date":
            end_date,
        "updated_at":
            now,
    })

    sync_general_user(
        db,
        user_name=user_name,
        email=current_email,
        tenant_id=tenant_id,
        start_date=current_start_date,
        end_date=end_date,
        updated_at=now,
    )

    return document_to_dict(
        document_reference.get()
    )


@router.delete(
    "/{admin_user_id}"
)
def delete_admin_user(
    admin_user_id: str,
    authorization: str = Header(...),
):
    authenticate_system_administrator(
        authorization
    )

    db = get_firestore_client()

    document_reference = (
        db.collection(
            ADMIN_COLLECTION
        )
        .document(
            admin_user_id
        )
    )

    document = document_reference.get()

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "管理ユーザーが"
                "見つかりません。"
            ),
        )

    current = (
        document.to_dict()
        or {}
    )

    email = normalize_email(
        current.get(
            "email",
            "",
        )
    )

    tenant_id = normalize_text(
        current.get(
            "tenant_id",
            "",
        )
    )

    if tenant_id:
        delete_general_users_for_admin(
            db,
            email=email,
            tenant_id=tenant_id,
        )

    document_reference.delete()

    return {
        "status":
            "deleted",
        "id":
            admin_user_id,
    }
