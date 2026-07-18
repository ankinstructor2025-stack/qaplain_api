import base64
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.routers.data_import_common import (
    build_requested_url,
    normalize_text,
    save_import_file,
    validate_common_data_source,
)


def execute_basic_import(
    *,
    data_source: dict,
    user: dict,
) -> dict:
    validate_common_data_source(
        data_source=data_source,
        expected_method_key="basic",
    )

    username = normalize_text(
        data_source.get(
            "username",
            "",
        )
    )
    password = str(
        data_source.get(
            "password",
            "",
        )
        or ""
    )

    if not username or not password:
        raise HTTPException(
            status_code=400,
            detail="Basic認証情報が設定されていません。",
        )

    requested_url = build_requested_url(
        data_source
    )

    encoded = base64.b64encode(
        f"{username}:{password}".encode(
            "utf-8"
        )
    ).decode(
        "ascii"
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
                f"Basic {encoded}",
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
        import_method="basic",
        user=user,
        source_url=requested_url,
        http_status=http_status,
    )
