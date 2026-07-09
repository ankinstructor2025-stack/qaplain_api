import firebase_admin
from firebase_admin import auth, credentials

FIREBASE_PROJECT_ID = "ank-firebase"

def initialize_firebase():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(
            options={
                "projectId": FIREBASE_PROJECT_ID
            }
        )

def verify_id_token(id_token: str) -> dict:
    return auth.verify_id_token(id_token)
