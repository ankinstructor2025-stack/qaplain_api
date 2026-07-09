import firebase_admin
from firebase_admin import auth

def initialize_firebase():
    """
    Firebase Admin SDK を初期化する。
    Cloud Runではサービスアカウントを自動利用する。
    """
    if not firebase_admin._apps:
        firebase_admin.initialize_app()

def verify_id_token(id_token: str) -> dict:
    """
    Firebase IDトークンを検証し、デコードした情報を返す。

    Returns:
        {
            "uid": "...",
            "email": "...",
            ...
        }
    """
    return auth.verify_id_token(id_token)
