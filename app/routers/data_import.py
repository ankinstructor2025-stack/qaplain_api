from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel, Field

from app.core.cloud_tasks import (
    authenticate_cloud_task,
)
from app.core.firebase import get_firestore_client
from app.routers.data_import_common import (
    DATA_IMPORT_COLLECTION,
    DATA_IMPORT_TASK_COLLECTION,
    UPLOADED_FILE_COLLECTION,
    authenticate_user,
    enqueue_import_task,
    get_data_source,
    get_import_task,
    normalize_key,
    normalize_text,
    now_iso,
    serialize_datetime,
    serialize_value,
    update_import_task,
)
from app.routers.data_import_file_upload import (
    execute_file_upload,
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


class DataImportWorkerRequest(
    BaseModel
):
    task_id: str = Field(
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


def execute_import_by_method(
    *,
    data_source: dict,
    user: dict,
) -> dict:
    method_key = normalize_key(
        data_source.get(
            "authentication_method_key",
            "",
        )
    )

    if method_key == "none":
        from app.routers.data_import_none import (
            execute_none_import,
        )

        return execute_none_import(
            data_source=data_source,
            user=user,
        )

    if method_key == "basic":
        from app.routers.data_import_basic import (
            execute_basic_import,
        )

        return execute_basic_import(
            data_source=data_source,
            user=user,
        )

    if method_key == "client_credentials":
        from app.routers.data_import_client_credentials import (
            execute_client_credentials_import,
        )

        return execute_client_credentials_import(
            data_source=data_source,
            user=user,
        )

    raise HTTPException(
        status_code=400,
        detail=(
            "未対応の認証方式です。"
            f" method={method_key}"
        ),
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
    status_code=202,
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

    if normalize_key(
        data_source.get(
            "authentication_method_key",
            "",
        )
    ) != "none":
        raise HTTPException(
            status_code=400,
            detail="認証なしのデータソースではありません。",
        )

    return enqueue_import_task(
        data_source=data_source,
        user=user,
    )


@router.post(
    "/basic",
    status_code=202,
)
def import_basic(
    request: DataImportRequest,
    authorization: str = Header(...),
):
    user = authenticate_user(
        authorization
    )
    data_source = get_data_source(
        request.data_source_id
    )

    if normalize_key(
        data_source.get(
            "authentication_method_key",
            "",
        )
    ) != "basic":
        raise HTTPException(
            status_code=400,
            detail="Basic認証のデータソースではありません。",
        )

    return enqueue_import_task(
        data_source=data_source,
        user=user,
    )


@router.post(
    "/client-credentials",
    status_code=202,
)
def import_client_credentials(
    request: DataImportRequest,
    authorization: str = Header(...),
):
    user = authenticate_user(
        authorization
    )
    data_source = get_data_source(
        request.data_source_id
    )

    if normalize_key(
        data_source.get(
            "authentication_method_key",
            "",
        )
    ) != "client_credentials":
        raise HTTPException(
            status_code=400,
            detail=(
                "Client Credentials認証の"
                "データソースではありません。"
            ),
        )

    return enqueue_import_task(
        data_source=data_source,
        user=user,
    )


@router.post(
    "/tasks/worker",
    include_in_schema=False,
)
def execute_import_worker(
    request: DataImportWorkerRequest,
    authorization: str = Header(...),
):
    authenticate_cloud_task(
        authorization
    )
    task_data = get_import_task(
        request.task_id
    )

    if task_data.get(
        "status"
    ) == "completed":
        return {
            "status": "already_completed",
            "task_id": request.task_id,
            "result_item_id": task_data.get(
                "result_item_id"
            ),
        }

    update_import_task(
        request.task_id,
        status="running",
        started_at=(
            task_data.get("started_at")
            or now_iso()
        ),
        error_message=None,
    )

    try:
        data_source = get_data_source(
            task_data[
                "data_source_id"
            ]
        )
        result = execute_import_by_method(
            data_source=data_source,
            user={
                "email": task_data.get(
                    "requested_by",
                    "",
                )
            },
        )
        result_item_id = (
            result.get("item_id")
            or result.get("file_id")
        )

        update_import_task(
            request.task_id,
            status="completed",
            result_item_id=result_item_id,
            completed_at=now_iso(),
            error_message=None,
        )

        return {
            "status": "completed",
            "task_id": request.task_id,
            "result_item_id": result_item_id,
        }

    except HTTPException as error:
        update_import_task(
            request.task_id,
            status="failed",
            error_message=str(error.detail),
            completed_at=now_iso(),
        )

        raise HTTPException(
            status_code=(
                error.status_code
                if error.status_code >= 500
                else 500
            ),
            detail=error.detail,
        )

    except Exception as error:
        update_import_task(
            request.task_id,
            status="failed",
            error_message=str(error),
            completed_at=now_iso(),
        )

        print(
            "data import worker error: "
            f"{type(error).__name__}: {error}"
        )

        raise HTTPException(
            status_code=500,
            detail="データ取得タスクの実行に失敗しました。",
        )


@router.get(
    "/tasks/{task_id}"
)
def get_task_status(
    task_id: str,
    authorization: str = Header(...),
):
    user = authenticate_user(
        authorization
    )
    task_data = get_import_task(
        task_id
    )

    if normalize_text(
        task_data.get(
            "requested_by",
            "",
        )
    ).lower() != normalize_text(
        user.get(
            "email",
            "",
        )
    ).lower():
        raise HTTPException(
            status_code=403,
            detail="この取込タスクを参照できません。",
        )

    return serialize_value(
        task_data
    )


@router.get(
    "/tasks"
)
def get_tasks(
    data_source_id: str | None = Query(
        None
    ),
    authorization: str = Header(...),
):
    user = authenticate_user(
        authorization
    )
    query = (
        get_firestore_client()
        .collection(
            DATA_IMPORT_TASK_COLLECTION
        )
        .where(
            "requested_by",
            "==",
            user["email"],
        )
    )

    if data_source_id:
        query = query.where(
            "data_source_id",
            "==",
            normalize_text(
                data_source_id
            ),
        )

    tasks = [
        serialize_value({
            **(document.to_dict() or {}),
            "task_id": document.id,
        })
        for document in query.stream()
    ]
    tasks.sort(
        key=lambda item:
            normalize_text(
                item.get(
                    "updated_at",
                    "",
                )
            ),
        reverse=True,
    )

    return {
        "tasks": tasks,
        "count": len(tasks),
    }


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
