from fastapi import HTTPException

from app.routers.data_source_common import (
    create_common_data,
    delete_connection_fields,
    normalize_text,
    set_external_connection_data,
)


def validate_none(request, is_update: bool) -> None:
    if not normalize_text(
        request.endpoint_url
    ):
        raise HTTPException(
            status_code=400,
            detail="接続先URLを入力してください。",
        )

def create_none_data(
    request,
    method_key: str,
    existing_data: dict,
) -> dict:
    data = create_common_data(
        request=request,
        method_key=method_key,
    )

    delete_connection_fields(data)
    set_external_connection_data(
        data=data,
        request=request,
    )

    return data
