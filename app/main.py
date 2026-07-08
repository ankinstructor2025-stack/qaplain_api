from fastapi import FastAPI
from app.routers.session import router as session_router
from app.core.cors import setup_cors

def create_app() -> FastAPI:
    app = FastAPI()
    setup_cors(app)

    app.include_router(session_router)
    return app

app = create_app()
