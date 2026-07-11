import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


router = APIRouter(
    prefix="/admin-users",
    tags=["admin-users"],
)

COLLECTION_NAME = "admin_users"


class AdminUserRequest(BaseModel):
    user_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    start_date: str
    end_date: Optional[str] = None


def authenticate_system_administrator(
    authorization: str,
) -> dict:

    if not authorization.startswith("Bearer "):
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
        decoded_token = verify_id_token(id_token)
    except Exception as e:
        print(
            f"verify_id_token error: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=401,
            detail=f"{type(e).__name__}: {e}",
        )

    email = decoded_token.get("email")

    if not email:
        raise HTTPException(
            status_code=401,
            detail="Email is not available",
        )

    system_administrator = os.getenv(
        "SYSTEM_ADMINISTRATOR",
        "",
    ).strip()

    if not system_administrator:
        raise HTTPException(
            status_code=500,
            detail="SYSTEM_ADMINISTRATOR is not configured",
        )

    if email.lower() != system_administrator.lower():
        raise HTTPException(
            status_code=403,
            detail="SYSTEM_ADMINISTRATOR permission is required",
        )

    return decoded_token


def validate_date_range(
    start_date: str,
    end_date: Optional[str],
) -> None:

    if end_date and start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="利用終了日は利用開始日以降にしてください。",
        )


def check_duplicate_email(
    email: str,
    exclude_id: Optional[str] = None,
) -> None:

    db = get_firestore_client()

    documents = (
        db.collection(COLLECTION_NAME)
        .where("email", "==", email.lower())
        .stream()
    )

    for document in documents:
        if document.id != exclude_id:
            raise HTTPException(
                status_code=409,
                detail="同じメールアドレスが既に登録されています。",
            )


def document_to_dict(document) -> dict:

    data = document.to_dict() or {}

    return {
        "id": document.id,
        "user_name": data.get("user_name", ""),
        "email": data.get("email", ""),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


@router.get("")
def get_admin_users(
    authorization: str = Header(...),
):

    authenticate_system_administrator(authorization)

    db = get_firestore_client()

    documents = (
        db.collection(COLLECTION_NAME)
        .order_by("user_name")
        .stream()
    )

    users = [
        document_to_dict(document)
        for document in documents
    ]

    return {
        "users": users,
    }


@router.get("/{admin_user_id}")
def get_admin_user(
    admin_user_id: str,
    authorization: str = Header(...),
):

    authenticate_system_administrator(authorization)

    db = get_firestore_client()

    document = (
        db.collection(COLLECTION_NAME)
        .document(admin_user_id)
        .get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail="管理ユーザーが見つかりません。",
        )

    return document_to_dict(document)


@router.post("", status_code=201)
def create_admin_user(
    request: AdminUserRequest,
    authorization: str = Header(...),
):

    authenticate_system_administrator(authorization)

    user_name = request.user_name.strip()
    email = str(request.email).strip().lower()
    start_date = request.start_date.strip()
    end_date = (
        request.end_date.strip()
        if request.end_date
        else None
    )

    validate_date_range(
        start_date,
        end_date,
    )

    check_duplicate_email(email)

    now = datetime.now(timezone.utc).isoformat()

    data = {
        "user_name": user_name,
        "email": email,
        "start_date": start_date,
        "end_date": end_date,
        "created_at": now,
        "updated_at": now,
    }

    db = get_firestore_client()

    document_reference = (
        db.collection(COLLECTION_NAME)
        .document()
    )

    document_reference.set(data)

    return {
        "id": document_reference.id,
        **data,
    }


@router.put("/{admin_user_id}")
def update_admin_user(
    admin_user_id: str,
    request: AdminUserRequest,
    authorization: str = Header(...),
):

    authenticate_system_administrator(authorization)

    db = get_firestore_client()

    document_reference = (
        db.collection(COLLECTION_NAME)
        .document(admin_user_id)
    )

    document = document_reference.get()

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail="管理ユーザーが見つかりません。",
        )

    user_name = request.user_name.strip()
    email = str(request.email).strip().lower()
    start_date = request.start_date.strip()
    end_date = (
        request.end_date.strip()
        if request.end_date
        else None
    )

    validate_date_range(
        start_date,
        end_date,
    )

    check_duplicate_email(
        email,
        exclude_id=admin_user_id,
    )

    data = {
        "user_name": user_name,
        "email": email,
        "start_date": start_date,
        "end_date": end_date,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    document_reference.update(data)

    updated_document = document_reference.get()

    return document_to_dict(updated_document)


@router.delete("/{admin_user_id}")
def delete_admin_user(
    admin_user_id: str,
    authorization: str = Header(...),
):

    authenticate_system_administrator(authorization)

    db = get_firestore_client()

    document_reference = (
        db.collection(COLLECTION_NAME)
        .document(admin_user_id)
    )

    document = document_reference.get()

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail="管理ユーザーが見つかりません。",
        )

    document_reference.delete()

    return {
        "status": "deleted",
        "id": admin_user_id,
    }
