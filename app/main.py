from fastapi import FastAPI

from app.core.cors import setup_cors
from app.core.firebase import initialize_firebase

from app.routers.session import router as session_router


def create_app() -> FastAPI:
    # Firebase Admin SDK 初期化
    initialize_firebase()

    # FastAPI
    app = FastAPI()

    # CORS設定
    setup_cors(app)

    # Router登録
    app.include_router(session_router)

    return app


app = create_app()
