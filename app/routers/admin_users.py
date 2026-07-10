from fastapi import APIRouter

from app.core.firebase import get_firestore_client


router = APIRouter()


@router.get("/admin-users")
def get_admin_users():
    db = get_firestore_client()

    users = []

    docs = db.collection("admin_users").stream()

    for doc in docs:
        data = doc.to_dict() or {}

        users.append({
            "id": doc.id,
            "user_name": data.get("user_name", ""),
            "email": data.get("email", ""),
            "start_date": data.get("start_date", ""),
            "end_date": data.get("end_date", "")
        })

    users.sort(
        key=lambda user: user["email"].lower()
    )

    return {
        "users": users
    }
