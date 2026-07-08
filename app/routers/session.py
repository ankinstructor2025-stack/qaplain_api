from fastapi import APIRouter, Header, HTTPException
import firebase_admin
from firebase_admin import auth

router = APIRouter()

@router.post("/session")
def create_session(authorization: str = Header(...)):

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    id_token = authorization.replace("Bearer ", "")

    decoded_token = auth.verify_id_token(id_token)

    uid = decoded_token["uid"]
    email = decoded_token.get("email")

    return {
        "uid": uid,
        "email": email,
    }
