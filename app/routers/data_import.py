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
    authenticate_user,
    get_data_import_collection,
    get_data_source,
    normalize_key,
    normalize_text,
    now_iso,
    serialize_datetime,
    serialize_value,
)
from app.routers.data_import_file_upload import (
    execute_file_upload,
)
from app.routers.data_import_executor import (
    enqueue_import_task,
    execute_import,
    get_import_task,
    update_import_task,
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
        result = execute_import(
            data_source=data_source,
            task_data=task_data,
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
    batch_id: str | None = Query(
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

    if batch_id:
        query = query.where(
            "batch_id",
            "==",
            normalize_text(
                batch_id
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

    status_counts = {
        "queued": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "enqueue_failed": 0,
        "other": 0,
    }

    for task in tasks:
        status = normalize_key(
            task.get(
                "status",
                "",
            )
        )

        if status in status_counts:
            status_counts[status] += 1
        else:
            status_counts["other"] += 1

    total_count = len(tasks)
    active_count = (
        status_counts["queued"]
        + status_counts["running"]
    )
    failed_count = (
        status_counts["failed"]
        + status_counts["enqueue_failed"]
    )
    finished_count = (
        status_counts["completed"]
        + failed_count
    )
    progress_percent = (
        round(
            finished_count
            / total_count
            * 100,
            1,
        )
        if total_count
        else 0.0
    )

    root_tasks = [
        task
        for task in tasks
        if normalize_key(
            task.get(
                "task_type",
                "",
            )
        ) == "fetch_root"
    ]

    detail_tasks = [
        task
        for task in tasks
        if normalize_key(
            task.get(
                "task_type",
                "",
            )
        ) != "fetch_root"
    ]

    def count_finished(
        target_tasks: list[dict],
    ) -> int:
        return sum(
            1
            for task in target_tasks
            if normalize_key(
                task.get(
                    "status",
                    "",
                )
            )
            in {
                "completed",
                "failed",
                "enqueue_failed",
            }
        )

    if total_count == 0:
        batch_status = "not_started"
    elif active_count > 0:
        batch_status = "running"
    elif failed_count > 0:
        batch_status = "failed"
    else:
        batch_status = "completed"

    updated_at = max(
        (
            normalize_text(
                task.get(
                    "updated_at",
                    "",
                )
            )
            for task in tasks
        ),
        default="",
    )

    return {
        "data_source_id": normalize_text(
            data_source_id
        ),
        "batch_id": normalize_text(
            batch_id
        ),
        "status": batch_status,
        "total_count": total_count,
        "completed_count": status_counts["completed"],
        "running_count": status_counts["running"],
        "queued_count": status_counts["queued"],
        "failed_count": failed_count,
        "finished_count": finished_count,
        "active_count": active_count,
        "active": active_count > 0,
        "progress_percent": progress_percent,
        "root_total": len(root_tasks),
        "root_completed": count_finished(
            root_tasks
        ),
        "detail_total": len(detail_tasks),
        "detail_completed": count_finished(
            detail_tasks
        ),
        "updated_at": updated_at,
        "status_counts": status_counts,
        "tasks": tasks,
        "count": total_count,
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
        get_data_import_collection(
            normalized_data_source_id
        ).stream()
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
