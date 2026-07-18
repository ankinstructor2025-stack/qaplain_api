from fastapi import HTTPException

from app.routers.data_source_common import (
    create_common_data,
    delete_connection_fields,
    normalize_key,
    normalize_text,
    set_external_connection_data,
    validate_file_extensions,
)


def validate_none(request, is_update: bool) -> None:
    if not normalize_text(
        request.endpoint_url
    ):
        raise HTTPException(
            status_code=400,
            detail="接続先URLを入力してください。",
        )

    data_format = normalize_key(
        request.data_format
    )

    if data_format not in (
        "json",
        "xml",
        "file",
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "データ形式はJSON、XML、"
                "ファイルのいずれかを選択してください。"
            ),
        )

    if data_format == "file":
        validate_file_extensions(
            request.file_extensions
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

    data_format = normalize_key(
        request.data_format
    )
    data["data_format"] = data_format

    if data_format == "file":
        data["file_extensions"] = (
            validate_file_extensions(
                request.file_extensions
            )
        )

    return data
