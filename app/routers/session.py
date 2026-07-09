from fastapi import APIRouter, Header, HTTPException
import os

from app.core.firebase import verify_id_token

router = APIRouter(prefix="/session", tags=["session"])


@router.post("")
def create_session(authorization: str = Header(...)):

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    id_token = authorization.replace("Bearer ", "").strip()

    try:
        decoded_token = verify_id_token(id_token)
    except Exception as e:
        print(f"verify_id_token error: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=401,
            detail=f"{type(e).__name__}: {e}"
        )

    uid = decoded_token.get("uid")
    email = decoded_token.get("email")

    if not email:
        raise HTTPException(status_code=401, detail="Email is not available")

    system_administrator = os.getenv("SYSTEM_ADMINISTRATOR", "").strip()

    is_system_administrator = (
        email.lower() == system_administrator.lower()
    )

    return {
        "uid": uid,
        "email": email,
        "is_system_administrator": is_system_administrator
    }
