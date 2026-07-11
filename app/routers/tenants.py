import os
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


router = APIRouter(
    prefix="/tenants",
    tags=["tenants"],
)

TENANT_COLLECTION = "tenants"
ADMIN_COLLECTION = "admin_users"


class TenantRequest(BaseModel):
    tenant_name: str = Field(
        min_length=1,
        max_length=100,
    )
    start_date: str
    end_date: Optional[str] = None


def now_iso() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


def normalize_email(
    email: str,
) -> str:

    return email.strip().lower()


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
                "利用終了日は利用開始日以降に"
                "してください。"
            ),
        )


def authenticate_user_administrator(
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
            detail=(
                f"{type(error).__name__}: "
                f"{error}"
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
            detail="Email is not available",
        )

    system_administrator = (
        normalize_email(
            os.getenv(
                "SYSTEM_ADMINISTRATOR",
                "",
            )
        )
    )

    if email == system_administrator:

        return {
            **decoded_token,
            "email": email,
        }

    today = date.today().isoformat()

    db = get_firestore_client()

    documents = (
        db.collection(
            ADMIN_COLLECTION
        )
        .where(
            "email",
            "==",
            email,
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

        start_date = data.get(
            "start_date"
        )

        end_date = data.get(
            "end_date"
        )

        is_started = (
            not start_date
            or start_date <= today
        )

        is_not_ended = (
            not end_date
            or end_date >= today
        )

        if (
            is_started
            and is_not_ended
        ):
            return {
                **decoded_token,
                "email": email,
            }

    raise HTTPException(
        status_code=403,
        detail="管理権限がありません。",
    )


def check_duplicate_tenant_name(
    tenant_name: str,
    parent_user: str,
    exclude_id: Optional[str] = None,
) -> None:

    db = get_firestore_client()

    documents = (
        db.collection(
            TENANT_COLLECTION
        )
        .where(
            "parent_user",
            "==",
            parent_user,
        )
        .where(
            "tenant_name",
            "==",
            tenant_name,
        )
        .stream()
    )

    for document in documents:

        if document.id != exclude_id:

            raise HTTPException(
                status_code=409,
                detail=(
                    "同じテナント名が"
                    "既に登録されています。"
                ),
            )


def get_owned_tenant(
    tenant_id: str,
    parent_user: str,
):

    db = get_firestore_client()

    document_reference = (
        db.collection(
            TENANT_COLLECTION
        )
        .document(
            tenant_id
        )
    )

    document = (
        document_reference.get()
    )

    if not document.exists:

        raise HTTPException(
            status_code=404,
            detail=(
                "テナントが"
                "見つかりません。"
            ),
        )

    data = document.to_dict() or {}

    stored_parent_user = (
        normalize_email(
            data.get(
                "parent_user",
                "",
            )
        )
    )

    if stored_parent_user != parent_user:

        raise HTTPException(
            status_code=403,
            detail=(
                "このテナントを"
                "操作する権限がありません。"
            ),
        )

    return (
        document_reference,
        document,
    )


def document_to_dict(
    document,
) -> dict:

    data = document.to_dict() or {}

    return {
        "id": document.id,
        "tenant_name": data.get(
            "tenant_name",
            "",
        ),
        "parent_user": data.get(
            "parent_user",
            "",
        ),
        "start_date": data.get(
            "start_date"
        ),
        "end_date": data.get(
            "end_date"
        ),
        "created_at": data.get(
            "created_at"
        ),
        "updated_at": data.get(
            "updated_at"
        ),
    }


@router.get("")
def get_tenants(
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_user_administrator(
            authorization
        )
    )

    parent_user = normalize_email(
        authenticated_user.get(
            "email",
            "",
        )
    )

    db = get_firestore_client()

    documents = (
        db.collection(
            TENANT_COLLECTION
        )
        .where(
            "parent_user",
            "==",
            parent_user,
        )
        .stream()
    )

    tenants = [
        document_to_dict(document)
        for document in documents
    ]

    tenants.sort(
        key=lambda tenant:
        tenant.get(
            "tenant_name",
            "",
        )
    )

    return {
        "tenants": tenants
    }


@router.get("/{tenant_id}")
def get_tenant(
    tenant_id: str,
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_user_administrator(
            authorization
        )
    )

    parent_user = normalize_email(
        authenticated_user.get(
            "email",
            "",
        )
    )

    _, document = get_owned_tenant(
        tenant_id,
        parent_user,
    )

    return document_to_dict(
        document
    )


@router.post(
    "",
    status_code=201,
)
def create_tenant(
    request: TenantRequest,
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_user_administrator(
            authorization
        )
    )

    parent_user = normalize_email(
        authenticated_user.get(
            "email",
            "",
        )
    )

    tenant_name = (
        request.tenant_name.strip()
    )

    start_date = (
        request.start_date.strip()
    )

    end_date = normalize_end_date(
        request.end_date
    )

    validate_date_range(
        start_date,
        end_date,
    )

    check_duplicate_tenant_name(
        tenant_name,
        parent_user,
    )

    now = now_iso()

    data = {
        "tenant_name": tenant_name,
        "parent_user": parent_user,
        "start_date": start_date,
        "end_date": end_date,
        "created_at": now,
        "updated_at": now,
    }

    db = get_firestore_client()

    document_reference = (
        db.collection(
            TENANT_COLLECTION
        )
        .document()
    )

    document_reference.set(
        data
    )

    return {
        "id": document_reference.id,
        **data,
    }


@router.put("/{tenant_id}")
def update_tenant(
    tenant_id: str,
    request: TenantRequest,
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_user_administrator(
            authorization
        )
    )

    parent_user = normalize_email(
        authenticated_user.get(
            "email",
            "",
        )
    )

    (
        document_reference,
        document,
    ) = get_owned_tenant(
        tenant_id,
        parent_user,
    )

    current = document.to_dict() or {}

    current_start_date = (
        current.get(
            "start_date",
            "",
        )
    )

    requested_start_date = (
        request.start_date.strip()
    )

    if (
        requested_start_date
        != current_start_date
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "利用開始日は"
                "変更できません。"
            ),
        )

    tenant_name = (
        request.tenant_name.strip()
    )

    end_date = normalize_end_date(
        request.end_date
    )

    validate_date_range(
        current_start_date,
        end_date,
    )

    check_duplicate_tenant_name(
        tenant_name,
        parent_user,
        exclude_id=tenant_id,
    )

    document_reference.update({
        "tenant_name": tenant_name,
        "end_date": end_date,
        "updated_at": now_iso(),
    })

    return document_to_dict(
        document_reference.get()
    )


@router.delete("/{tenant_id}")
def delete_tenant(
    tenant_id: str,
    authorization: str = Header(...),
):

    authenticated_user = (
        authenticate_user_administrator(
            authorization
        )
    )

    parent_user = normalize_email(
        authenticated_user.get(
            "email",
            "",
        )
    )

    (
        document_reference,
        _,
    ) = get_owned_tenant(
        tenant_id,
        parent_user,
    )

    document_reference.delete()

    return {
        "status": "deleted",
        "id": tenant_id,
    }
