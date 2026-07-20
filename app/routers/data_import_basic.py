import base64

from fastapi import HTTPException

from app.routers.data_import_common import normalize_text, validate_common_data_source


def build_auth_headers(data_source: dict) -> dict[str, str]:
    validate_common_data_source(data_source, "basic")

    username = normalize_text(data_source.get("username", ""))
    password = str(data_source.get("password", "") or "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Basic認証情報が設定されていません。")

    encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}
