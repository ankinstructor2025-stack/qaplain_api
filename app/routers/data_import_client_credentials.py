import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.routers.data_import_common import normalize_text, validate_common_data_source


def get_access_token(data_source: dict) -> str:
    token_url = normalize_text(data_source.get("token_url", ""))
    client_id = normalize_text(data_source.get("client_id", ""))
    client_secret = str(data_source.get("client_secret", "") or "")
    scope = normalize_text(data_source.get("scope", ""))

    if not token_url:
        raise HTTPException(status_code=400, detail="トークンURLが設定されていません。")
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Client Credentials認証情報が設定されていません。")

    token_values = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        token_values["scope"] = scope

    request = Request(
        token_url,
        data=urlencode(token_values).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=60) as response:
            token_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "アクセストークンを取得できませんでした。",
                "external_status": error.code,
                "external_detail": error.read().decode("utf-8", errors="replace")[:1000],
            },
        )
    except (URLError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=502, detail=f"アクセストークンを取得できませんでした。 {error}")

    access_token = normalize_text(token_data.get("access_token", ""))
    if not access_token:
        raise HTTPException(status_code=502, detail="トークンレスポンスにaccess_tokenがありません。")
    return access_token


def build_auth_headers(data_source: dict) -> dict[str, str]:
    validate_common_data_source(data_source, "client_credentials")
    return {"Authorization": f"Bearer {get_access_token(data_source)}"}
