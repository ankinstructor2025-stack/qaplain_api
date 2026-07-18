from app.routers.data_source_common import (
    create_common_data,
    delete_connection_fields,
    validate_file_extensions,
)


def validate_file_upload(request, is_update: bool) -> None:
    validate_file_extensions(
        request.file_extensions
    )


def create_file_upload_data(
    request,
    method_key: str,
    existing_data: dict,
) -> dict:
    data = create_common_data(
        request=request,
        method_key=method_key,
    )

    delete_connection_fields(data)

    data["data_format"] = "file"

    data["file_extensions"] = (
        validate_file_extensions(
            request.file_extensions
        )
    )

    return data
