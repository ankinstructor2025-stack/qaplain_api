from fastapi import APIRouter, Header, HTTPException

from app.core.firebase import verify_id_token

router = APIRouter(prefix="/session", tags=["session"])


@router.post("")
def create_session(authorization: str = Header(...)):

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    id_token = authorization.replace("Bearer ", "")

    decoded_token = verify_id_token(id_token)

    return {
        "uid": decoded_token["uid"],
        "email": decoded_token.get("email"),
    }
