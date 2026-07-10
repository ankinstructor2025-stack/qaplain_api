from fastapi import APIRouter
from firebase_admin import firestore

router = APIRouter(prefix="", tags=["admin-users"])


@router.get("/admin-users")
def get_admin_users():

    db = firestore.client()

    docs = (
        db.collection("admin_users")
        .order_by("email")
        .stream()
    )

    users = []

    for doc in docs:

        data = doc.to_dict() or {}

        users.append({
            "id": doc.id,
            "user_name": data.get("user_name", ""),
            "email": data.get("email", ""),
            "start_date": data.get("start_date", ""),
            "end_date": data.get("end_date", "")
        })

    return {
        "users": users
    }
