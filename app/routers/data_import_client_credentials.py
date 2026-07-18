import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.routers.data_import_common import (
    build_requested_url,
    normalize_text,
    save_import_file,
    validate_common_data_source,
)


def get_access_token(
    data_source: dict,
) -> str:
    token_url = normalize_text(
        data_source.get(
            "token_url",
            "",
        )
    )
    client_id = normalize_text(
        data_source.get(
            "client_id",
            "",
        )
    )
    client_secret = str(
        data_source.get(
            "client_secret",
            "",
        )
        or ""
    )
    scope = normalize_text(
        data_source.get(
            "scope",
            "",
        )
    )

    if not token_url:
        raise HTTPException(
            status_code=400,
            detail="トークンURLが設定されていません。",
        )

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Client Credentials認証情報が設定されていません。",
        )

    token_values = {
        "grant_type":
            "client_credentials",
        "client_id":
            client_id,
        "client_secret":
            client_secret,
    }

    if scope:
        token_values["scope"] = scope

    request = Request(
        token_url,
        data=urlencode(
            token_values
        ).encode(
            "utf-8"
        ),
        method="POST",
        headers={
            "Content-Type":
                "application/x-www-form-urlencoded",
            "Accept":
                "application/json",
        },
    )

    try:
        with urlopen(
            request,
            timeout=60,
        ) as response:
            token_data = json.loads(
                response.read()
                .decode(
                    "utf-8"
                )
            )

    except HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "message":
                    "アクセストークンを取得できませんでした。",
                "external_status":
                    error.code,
                "external_detail":
                    error.read()
                    .decode(
                        "utf-8",
                        errors="replace",
                    )[:1000],
            },
        )

    except (
        URLError,
        json.JSONDecodeError,
    ) as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "アクセストークンを取得できませんでした。"
                f" {error}"
            ),
        )

    access_token = normalize_text(
        token_data.get(
            "access_token",
            "",
        )
    )

    if not access_token:
        raise HTTPException(
            status_code=502,
            detail="トークンレスポンスにaccess_tokenがありません。",
        )

    return access_token


def execute_client_credentials_import(
    *,
    data_source: dict,
    user: dict,
) -> dict:
    validate_common_data_source(
        data_source=data_source,
        expected_method_key="client_credentials",
    )

    access_token = get_access_token(
        data_source
    )
    requested_url = build_requested_url(
        data_source
    )

    request = Request(
        requested_url,
        method="GET",
        headers={
            "User-Agent":
                "QAPlain-Knowledge-Studio/1.0",
            "Accept":
                "*/*",
            "Authorization":
                f"Bearer {access_token}",
        },
    )

    try:
        with urlopen(
            request,
            timeout=60,
        ) as response:
            content = response.read()
            http_status = int(
                response.status
            )
            content_type = (
                response.headers.get(
                    "Content-Type",
                    "application/octet-stream",
                )
                .split(";", 1)[0]
                .strip()
                .lower()
            )

    except HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "message":
                    "接続先APIからエラーが返されました。",
                "external_status":
                    error.code,
                "external_detail":
                    error.read()
                    .decode(
                        "utf-8",
                        errors="replace",
                    )[:1000],
            },
        )

    except URLError as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "接続先APIへ接続できませんでした。"
                f" {error.reason}"
            ),
        )

    return save_import_file(
        content=content,
        content_type=content_type,
        data_source=data_source,
        import_method="client_credentials",
        user=user,
        source_url=requested_url,
        http_status=http_status,
    )
