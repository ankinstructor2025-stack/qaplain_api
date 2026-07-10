import firebase_admin

from firebase_admin import auth
from firebase_admin import firestore


# Firebase Authentication
FIREBASE_AUTH_PROJECT_ID = "ank-firebase"

# Cloud Firestore
FIRESTORE_PROJECT_ID = "utopian-catfish-484423-v0"
FIRESTORE_DATABASE_ID = "qaplaindb"

FIRESTORE_APP_NAME = "firestore-app"


def initialize_firebase() -> None:
    """
    Firebase Authentication用とFirestore用の
    Firebase Adminアプリを初期化する。
    """

    # Firebase Authentication用
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(
            options={
                "projectId": FIREBASE_AUTH_PROJECT_ID
            }
        )

    # Firestore用
    try:
        firebase_admin.get_app(FIRESTORE_APP_NAME)
    except ValueError:
        firebase_admin.initialize_app(
            options={
                "projectId": FIRESTORE_PROJECT_ID
            },
            name=FIRESTORE_APP_NAME
        )


def verify_id_token(id_token: str) -> dict:
    """
    ank-firebaseで発行されたIDトークンを検証する。
    """

    auth_app = firebase_admin.get_app()

    return auth.verify_id_token(
        id_token,
        app=auth_app
    )


def get_firestore_client():
    """
    My First Project内のqaplaindbへ接続する
    Firestoreクライアントを返す。
    """

    firestore_app = firebase_admin.get_app(
        FIRESTORE_APP_NAME
    )

    return firestore.client(
        app=firestore_app,
        database_id=FIRESTORE_DATABASE_ID
    )
