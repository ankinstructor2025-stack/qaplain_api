import os
from datetime import date

from fastapi import APIRouter, Header, HTTPException

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


router = APIRouter(
    prefix="/session",
    tags=["session"],
)

GENERAL_USERS_COLLECTION = "general_users"


@router.post("")
def create_session(
    authorization: str = Header(...),
):

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

    uid = decoded_token.get("uid")
    email = decoded_token.get("email")

    if not email:
        raise HTTPException(
            status_code=401,
            detail="Email is not available",
        )

    email = email.strip().lower()

    system_administrator = os.getenv(
        "SYSTEM_ADMINISTRATOR",
        "",
    ).strip().lower()

    is_system_administrator = (
        email == system_administrator
    )

    is_admin_user = False

    db = get_firestore_client()

    documents = (
        db.collection(GENERAL_USERS_COLLECTION)
        .where("email", "==", email)
        .limit(1)
        .stream()
    )

    document = next(
        documents,
        None,
    )

    if document:

        data = document.to_dict() or {}

        user_type = data.get(
            "user_type",
            "GENERAL",
        )

        start_date = data.get(
            "start_date"
        )

        end_date = data.get(
            "end_date"
        )

        today = date.today().isoformat()

        is_active = (
            (not start_date or start_date <= today)
            and
            (not end_date or end_date >= today)
        )

        is_admin_user = (
            user_type == "ADMIN"
            and
            is_active
        )

    can_manage_users = (
        is_system_administrator
        or
        is_admin_user
    )

    return {
        "uid": uid,
        "email": email,
        "is_system_administrator": (
            is_system_administrator
        ),
        "is_admin_user": is_admin_user,
        "can_manage_users": can_manage_users,
    }
