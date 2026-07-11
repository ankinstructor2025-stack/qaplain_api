import os
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.firebase import get_firestore_client, verify_id_token


router = APIRouter(
    prefix="/general-users",
    tags=["general-users"],
)

GENERAL_COLLECTION = "general_users"
ADMIN_COLLECTION = "admin_users"


class GeneralUserRequest(BaseModel):
    user_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=1, max_length=200)
    user_type: Literal["ADMIN", "GENERAL"] = "GENERAL"
    start_date: str
    end_date: Optional[str] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_end_date(end_date: Optional[str]) -> Optional[str]:
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

    if end_date and start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="利用終了日は利用開始日以降にしてください。",
        )


def authenticate_user_administrator(
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

    email = normalize_email(
        decoded_token.get("email", "")
    )

    if not email:
        raise HTTPException(
            status_code=401,
            detail="Email is not available",
        )

    system_administrator = normalize_email(
        os.getenv(
            "SYSTEM_ADMINISTRATOR",
            "",
        )
    )

    if email == system_administrator:
        return decoded_token

    today = date.today().isoformat()
    db = get_firestore_client()

    documents = (
        db.collection(ADMIN_COLLECTION)
        .where("email", "==", email)
        .limit(1)
        .stream()
    )

    document = next(documents, None)

    if document:
        data = document.to_dict() or {}
        start_date = data.get("start_date")
        end_date = data.get("end_date")

        if (
            (not start_date or start_date <= today)
            and
            (not end_date or end_date >= today)
        ):
            return decoded_token

    raise HTTPException(
        status_code=403,
        detail="管理権限がありません。",
    )


def check_duplicate_email(
    email: str,
    exclude_id: Optional[str] = None,
) -> None:
    db = get_firestore_client()

    documents = (
        db.collection(GENERAL_COLLECTION)
        .where("email", "==", email)
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
        "user_type": data.get(
            "user_type",
            "GENERAL",
        ),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


@router.get("")
def get_general_users(
    authorization: str = Header(...),
):
    authenticate_user_administrator(authorization)

    db = get_firestore_client()

    documents = (
        db.collection(GENERAL_COLLECTION)
        .order_by("user_name")
        .stream()
    )

    return {
        "users": [
            document_to_dict(document)
            for document in documents
        ]
    }


@router.get("/{general_user_id}")
def get_general_user(
    general_user_id: str,
    authorization: str = Header(...),
):
    authenticate_user_administrator(authorization)

    db = get_firestore_client()

    document = (
        db.collection(GENERAL_COLLECTION)
        .document(general_user_id)
        .get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail="一般ユーザーが見つかりません。",
        )

    return document_to_dict(document)


@router.post("", status_code=201)
def create_general_user(
    request: GeneralUserRequest,
    authorization: str = Header(...),
):
    authenticate_user_administrator(authorization)

    user_name = request.user_name.strip()
    email = normalize_email(request.email)
    user_type = request.user_type
    start_date = request.start_date.strip()
    end_date = normalize_end_date(request.end_date)

    validate_date_range(start_date, end_date)
    check_duplicate_email(email)

    now = now_iso()

    data = {
        "user_name": user_name,
        "email": email,
        "user_type": user_type,
        "start_date": start_date,
        "end_date": end_date,
        "created_at": now,
        "updated_at": now,
    }

    db = get_firestore_client()
    document_reference = (
        db.collection(GENERAL_COLLECTION)
        .document()
    )

    document_reference.set(data)

    return {
        "id": document_reference.id,
        **data,
    }


@router.put("/{general_user_id}")
def update_general_user(
    general_user_id: str,
    request: GeneralUserRequest,
    authorization: str = Header(...),
):
    authenticate_user_administrator(authorization)

    db = get_firestore_client()
    document_reference = (
        db.collection(GENERAL_COLLECTION)
        .document(general_user_id)
    )
    document = document_reference.get()

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail="一般ユーザーが見つかりません。",
        )

    user_name = request.user_name.strip()
    email = normalize_email(request.email)
    user_type = request.user_type
    start_date = request.start_date.strip()
    end_date = normalize_end_date(request.end_date)

    validate_date_range(start_date, end_date)
    check_duplicate_email(
        email,
        exclude_id=general_user_id,
    )

    document_reference.update({
        "user_name": user_name,
        "email": email,
        "user_type": user_type,
        "start_date": start_date,
        "end_date": end_date,
        "updated_at": now_iso(),
    })

    return document_to_dict(
        document_reference.get()
    )


@router.delete("/{general_user_id}")
def delete_general_user(
    general_user_id: str,
    authorization: str = Header(...),
):
    authenticate_user_administrator(authorization)

    db = get_firestore_client()
    document_reference = (
        db.collection(GENERAL_COLLECTION)
        .document(general_user_id)
    )

    if not document_reference.get().exists:
        raise HTTPException(
            status_code=404,
            detail="一般ユーザーが見つかりません。",
        )

    document_reference.delete()

    return {
        "status": "deleted",
        "id": general_user_id,
    }
