from fastapi import HTTPException

from app.routers.data_source_common import (
    normalize_text,
)
from app.routers.data_source_none import (
    create_none_data,
)


def validate_basic(request, is_update: bool) -> None:
    if not normalize_text(
        request.endpoint_url
    ):
        raise HTTPException(
            status_code=400,
            detail="接続先URLを入力してください。",
        )

    if not normalize_text(
        request.username
    ):
        raise HTTPException(
            status_code=400,
            detail="ユーザーIDを入力してください。",
        )

    if (
        not is_update
        and not request.password
    ):
        raise HTTPException(
            status_code=400,
            detail="パスワードを入力してください。",
        )


def create_basic_data(
    request,
    method_key: str,
    existing_data: dict,
) -> dict:
    data = create_none_data(
        request=request,
        method_key=method_key,
        existing_data=existing_data,
    )

    data["username"] = normalize_text(
        request.username
    )

    if request.password:
        data["password"] = request.password

    elif (
        existing_data.get(
            "authentication_method_key"
        )
        == "basic"
    ):
        existing_password = existing_data.get(
            "password"
        )

        if existing_password:
            data["password"] = existing_password

    return data
