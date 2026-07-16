from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    Query,
    UploadFile,
)
from pydantic import BaseModel, Field

from app.core.firebase import get_firestore_client
from app.routers.data_import_common import (
    DATA_IMPORT_COLLECTION,
    UPLOADED_FILE_COLLECTION,
    authenticate_user,
    get_data_source,
    normalize_text,
    serialize_datetime,
    serialize_value,
)
from app.routers.data_import_file_upload import (
    execute_file_upload,
)
from app.routers.data_import_none import (
    execute_none_import,
)


router = APIRouter(
    prefix="/data-import",
    tags=["data-import"],
)


class DataImportRequest(
    BaseModel
):
    data_source_id: str = Field(
        min_length=1,
    )


def serialize_uploaded_file(
    document,
) -> dict:
    data = document.to_dict() or {}

    return {
        "file_id":
            data.get(
                "file_id",
                document.id,
            ),

        "data_source_id":
            data.get(
                "data_source_id",
                "",
            ),

        "data_source_name":
            data.get(
                "data_source_name",
                "",
            ),

        "file_name":
            data.get(
                "file_name",
                "",
            ),

        "extension":
            data.get(
                "extension",
                "",
            ),

        "content_type":
            data.get(
                "content_type",
                "",
            ),

        "size_bytes":
            data.get(
                "size_bytes"
            ),

        "bucket_name":
            data.get(
                "bucket_name",
                "",
            ),

        "gcs_path":
            data.get(
                "gcs_path",
                "",
            ),

        "status":
            data.get(
                "status",
                "uploaded",
            ),

        "created_at":
            serialize_datetime(
                data.get(
                    "created_at"
                )
            ),

        "created_by":
            data.get(
                "created_by",
                "",
            ),

        "updated_at":
            serialize_datetime(
                data.get(
                    "updated_at"
                )
            ),

        "updated_by":
            data.get(
                "updated_by",
                "",
            ),
    }


def serialize_import(
    document,
) -> dict:
    data = document.to_dict() or {}

    data["item_id"] = (
        data.get(
            "item_id"
        )
        or document.id
    )

    return serialize_value(
        data
    )


@router.post(
    "/file-upload",
    status_code=201,
)
async def import_file_upload(
    overwrite: bool = Form(False),
    file: UploadFile = File(...),
    authorization: str = Header(...),
):
    user = authenticate_user(
        authorization
    )

    return execute_file_upload(
        upload_file=file,
        overwrite=overwrite,
        user=user,
    )


@router.post(
    "/none",
    status_code=201,
)
def import_none(
    request: DataImportRequest,
    authorization: str = Header(...),
):
    user = authenticate_user(
        authorization
    )

    data_source = get_data_source(
        request.data_source_id
    )

    return execute_none_import(
        data_source=data_source,
        user=user,
    )


@router.get(
    "/uploaded-files"
)
def get_uploaded_files(
    authorization: str = Header(...),
):
    authenticate_user(
        authorization
    )

    documents = (
        get_firestore_client()
        .collection(
            UPLOADED_FILE_COLLECTION
        )
        .stream()
    )

    uploaded_files = []

    for document in documents:
        data = document.to_dict() or {}

        if data.get(
            "deleted",
            False,
        ):
            continue

        uploaded_files.append(
            serialize_uploaded_file(
                document
            )
        )

    uploaded_files.sort(
        key=lambda item:
            item.get(
                "updated_at"
            )
            or item.get(
                "created_at"
            )
            or "",
        reverse=True,
    )

    return {
        "uploaded_files":
            uploaded_files,

        "count":
            len(
                uploaded_files
            ),
    }


@router.get(
    "/items"
)
def get_imports(
    data_source_id: str = Query(
        ...,
        min_length=1,
    ),
    authorization: str = Header(...),
):
    authenticate_user(
        authorization
    )

    normalized_data_source_id = (
        normalize_text(
            data_source_id
        )
    )

    documents = (
        get_firestore_client()
        .collection(
            DATA_IMPORT_COLLECTION
        )
        .where(
            "data_source_id",
            "==",
            normalized_data_source_id,
        )
        .stream()
    )

    imports = []

    for document in documents:
        item = serialize_import(
            document
        )

        if item.get(
            "deleted",
            False,
        ):
            continue

        imports.append(
            item
        )

    imports.sort(
        key=lambda item:
            normalize_text(
                item.get(
                    "updated_at"
                )
                or item.get(
                    "created_at"
                )
                or ""
            ),
        reverse=True,
    )

    return {
        "data_source_id":
            normalized_data_source_id,

        "latest_item":
            (
                imports[0]
                if imports
                else None
            ),

        "items":
            imports,

        "count":
            len(
                imports
            ),
    }
