import os
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.firebase import get_firestore_client, verify_id_token

router = APIRouter(prefix="/general-users", tags=["general-users"])

GENERAL_COLLECTION = "general_users"
TENANT_COLLECTION = "tenants"


class GeneralUserRequest(BaseModel):
    user_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=200)
    user_type: Literal["ADMIN", "GENERAL"] = "GENERAL"
    start_date: str
    end_date: Optional[str] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value) -> str:
    return str(value or "").strip()


def normalize_email(value) -> str:
    return normalize_text(value).lower()


def normalize_end_date(value: Optional[str]) -> Optional[str]:
    return normalize_text(value) or None


def validate_date_range(start_date: str, end_date: Optional[str]) -> None:
    if not start_date:
        raise HTTPException(status_code=400, detail="利用開始日を入力してください。")
    if end_date and start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="利用終了日は利用開始日以降にしてください。",
        )


def get_tenant_document(tenant_id: str):
    tenant_id = normalize_text(tenant_id)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="テナントを選択してください。")

    document = (
        get_firestore_client()
        .collection(TENANT_COLLECTION)
        .document(tenant_id)
        .get()
    )
    if not document.exists:
        raise HTTPException(status_code=400, detail="指定されたテナントが見つかりません。")
    return document


def tenant_to_dict(document) -> dict:
    data = document.to_dict() or {}
    return {
        "tenant_id": document.id,
        "tenant_name": data.get("tenant_name", ""),
        "parent_user": data.get("parent_user", ""),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
    }


def find_tenant_by_parent_user(email: str):
    documents = (
        get_firestore_client()
        .collection(TENANT_COLLECTION)
        .where("parent_user", "==", normalize_email(email))
        .limit(1)
        .stream()
    )
    return next(documents, None)


def resolve_user_tenant_id(user_data: dict) -> str:
    tenant_id = normalize_text(user_data.get("tenant_id"))
    if tenant_id:
        return tenant_id

    parent_user = normalize_email(user_data.get("parent_user"))
    if not parent_user:
        return ""

    tenant_document = find_tenant_by_parent_user(parent_user)
    return tenant_document.id if tenant_document else ""


def authenticate_user_administrator(authorization: str) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    id_token = authorization.replace("Bearer ", "", 1).strip()
    try:
        decoded_token = verify_id_token(id_token)
    except Exception as error:
        print(f"verify_id_token error: {type(error).__name__}: {error}")
        raise HTTPException(status_code=401, detail="認証情報を確認できませんでした。")

    email = normalize_email(decoded_token.get("email"))
    if not email:
        raise HTTPException(status_code=401, detail="メールアドレスを取得できませんでした。")

    system_administrator = normalize_email(os.getenv("SYSTEM_ADMINISTRATOR", ""))
    if email == system_administrator:
        return {
            "uid": decoded_token.get("uid"),
            "email": email,
            "is_system_administrator": True,
            "tenant_id": "",
        }

    today = date.today().isoformat()
    documents = (
        get_firestore_client()
        .collection(GENERAL_COLLECTION)
        .where("email", "==", email)
        .limit(1)
        .stream()
    )
    document = next(documents, None)
    if not document:
        raise HTTPException(status_code=403, detail="管理権限がありません。")

    data = document.to_dict() or {}
    is_active = (
        (not data.get("start_date") or data.get("start_date") <= today)
        and (not data.get("end_date") or data.get("end_date") >= today)
    )
    if data.get("user_type") != "ADMIN" or not is_active:
        raise HTTPException(status_code=403, detail="管理権限がありません。")

    tenant_id = resolve_user_tenant_id(data)
    if not tenant_id:
        raise HTTPException(
            status_code=403,
            detail="所属テナントが設定されていません。システム管理者へ確認してください。",
        )

    return {
        "uid": decoded_token.get("uid"),
        "email": email,
        "is_system_administrator": False,
        "tenant_id": tenant_id,
    }


def check_duplicate_email(email: str, exclude_id: Optional[str] = None) -> None:
    documents = (
        get_firestore_client()
        .collection(GENERAL_COLLECTION)
        .where("email", "==", email)
        .stream()
    )
    for document in documents:
        if document.id != exclude_id:
            raise HTTPException(
                status_code=409,
                detail="同じメールアドレスが既に登録されています。",
            )


def get_tenant_name(tenant_id: str) -> str:
    if not tenant_id:
        return ""
    document = (
        get_firestore_client()
        .collection(TENANT_COLLECTION)
        .document(tenant_id)
        .get()
    )
    if not document.exists:
        return ""
    return (document.to_dict() or {}).get("tenant_name", "")


def document_to_dict(document) -> dict:
    data = document.to_dict() or {}
    tenant_id = resolve_user_tenant_id(data)
    return {
        "id": document.id,
        "user_name": data.get("user_name", ""),
        "email": data.get("email", ""),
        "tenant_id": tenant_id,
        "tenant_name": get_tenant_name(tenant_id),
        "user_type": data.get("user_type", "GENERAL"),
        "parent_user": data.get("parent_user", ""),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def validate_access(actor: dict, document) -> dict:
    data = document.to_dict() or {}
    if actor["is_system_administrator"]:
        return data

    if resolve_user_tenant_id(data) != actor["tenant_id"]:
        raise HTTPException(status_code=403, detail="このユーザーを管理する権限がありません。")
    return data


def validate_requested_tenant(actor: dict, tenant_id: str) -> str:
    tenant_id = normalize_text(tenant_id)
    get_tenant_document(tenant_id)

    if not actor["is_system_administrator"] and tenant_id != actor["tenant_id"]:
        raise HTTPException(
            status_code=403,
            detail="所属テナント以外のユーザーは登録できません。",
        )
    return tenant_id


@router.get("/available-tenants")
def get_available_tenants(authorization: str = Header(...)):
    actor = authenticate_user_administrator(authorization)
    collection = get_firestore_client().collection(TENANT_COLLECTION)

    if actor["is_system_administrator"]:
        documents = collection.order_by("tenant_name").stream()
    else:
        document = collection.document(actor["tenant_id"]).get()
        documents = [document] if document.exists else []

    return {"tenants": [tenant_to_dict(document) for document in documents]}


@router.get("")
def get_general_users(authorization: str = Header(...)):
    actor = authenticate_user_administrator(authorization)
    collection = get_firestore_client().collection(GENERAL_COLLECTION)

    if actor["is_system_administrator"]:
        documents = list(collection.order_by("user_name").stream())
    else:
        documents = list(
            collection.where("tenant_id", "==", actor["tenant_id"]).stream()
        )
        if not documents:
            documents = list(
                collection.where("parent_user", "==", actor["email"]).stream()
            )

    return {"users": [document_to_dict(document) for document in documents]}


@router.get("/{general_user_id}")
def get_general_user(general_user_id: str, authorization: str = Header(...)):
    actor = authenticate_user_administrator(authorization)
    document = (
        get_firestore_client()
        .collection(GENERAL_COLLECTION)
        .document(general_user_id)
        .get()
    )
    if not document.exists:
        raise HTTPException(status_code=404, detail="一般ユーザーが見つかりません。")
    validate_access(actor, document)
    return document_to_dict(document)


@router.post("", status_code=201)
def create_general_user(request: GeneralUserRequest, authorization: str = Header(...)):
    actor = authenticate_user_administrator(authorization)

    if not actor["is_system_administrator"] and request.user_type == "ADMIN":
        raise HTTPException(status_code=403, detail="管理者権限のユーザーは作成できません。")

    tenant_id = validate_requested_tenant(actor, request.tenant_id)
    user_name = request.user_name.strip()
    email = normalize_email(request.email)
    start_date = request.start_date.strip()
    end_date = normalize_end_date(request.end_date)

    validate_date_range(start_date, end_date)
    check_duplicate_email(email)

    parent_user = email if request.user_type == "ADMIN" else actor["email"]
    now = now_iso()
    data = {
        "user_name": user_name,
        "email": email,
        "tenant_id": tenant_id,
        "user_type": request.user_type,
        "parent_user": parent_user,
        "start_date": start_date,
        "end_date": end_date,
        "created_at": now,
        "updated_at": now,
    }

    reference = get_firestore_client().collection(GENERAL_COLLECTION).document()
    reference.set(data)
    return {"id": reference.id, **data}


@router.put("/{general_user_id}")
def update_general_user(
    general_user_id: str,
    request: GeneralUserRequest,
    authorization: str = Header(...),
):
    actor = authenticate_user_administrator(authorization)
    reference = (
        get_firestore_client()
        .collection(GENERAL_COLLECTION)
        .document(general_user_id)
    )
    document = reference.get()
    if not document.exists:
        raise HTTPException(status_code=404, detail="一般ユーザーが見つかりません。")

    current = validate_access(actor, document)
    if not actor["is_system_administrator"] and request.user_type == "ADMIN":
        raise HTTPException(status_code=403, detail="管理者権限は設定できません。")

    tenant_id = validate_requested_tenant(actor, request.tenant_id)
    email = normalize_email(request.email)
    start_date = request.start_date.strip()
    end_date = normalize_end_date(request.end_date)

    validate_date_range(start_date, end_date)
    check_duplicate_email(email, exclude_id=general_user_id)

    parent_user = current.get("parent_user", actor["email"])
    if request.user_type == "ADMIN":
        parent_user = email

    reference.update({
        "user_name": request.user_name.strip(),
        "email": email,
        "tenant_id": tenant_id,
        "user_type": request.user_type,
        "parent_user": parent_user,
        "start_date": start_date,
        "end_date": end_date,
        "updated_at": now_iso(),
    })
    return document_to_dict(reference.get())


@router.delete("/{general_user_id}")
def delete_general_user(general_user_id: str, authorization: str = Header(...)):
    actor = authenticate_user_administrator(authorization)
    reference = (
        get_firestore_client()
        .collection(GENERAL_COLLECTION)
        .document(general_user_id)
    )
    document = reference.get()
    if not document.exists:
        raise HTTPException(status_code=404, detail="一般ユーザーが見つかりません。")
    validate_access(actor, document)
    reference.delete()
    return {"status": "deleted", "id": general_user_id}
