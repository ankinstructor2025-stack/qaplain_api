from fastapi import HTTPException

from app.routers.data_source_common import (
    normalize_text,
)
from app.routers.data_source_none import (
    create_none_data,
)


def validate_client_credentials(
    request,
    is_update: bool,
) -> None:
    if not normalize_text(
        request.endpoint_url
    ):
        raise HTTPException(
            status_code=400,
            detail="接続先URLを入力してください。",
        )

    if not normalize_text(
        request.client_id
    ):
        raise HTTPException(
            status_code=400,
            detail="クライアントIDを入力してください。",
        )

    if (
        not is_update
        and not request.client_secret
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "クライアントシークレットを"
                "入力してください。"
            ),
        )

    if not normalize_text(
        request.token_url
    ):
        raise HTTPException(
            status_code=400,
            detail="トークンURLを入力してください。",
        )


def create_client_credentials_data(
    request,
    method_key: str,
    existing_data: dict,
) -> dict:
    data = create_none_data(
        request=request,
        method_key=method_key,
        existing_data=existing_data,
    )

    data["client_id"] = normalize_text(
        request.client_id
    )
    data["token_url"] = normalize_text(
        request.token_url
    )
    data["scope"] = normalize_text(
        request.scope
    )

    if request.client_secret:
        data["client_secret"] = (
            request.client_secret
        )

    elif (
        existing_data.get(
            "authentication_method_key"
        )
        == "client_credentials"
    ):
        existing_secret = existing_data.get(
            "client_secret"
        )

        if existing_secret:
            data["client_secret"] = (
                existing_secret
            )

    return data
