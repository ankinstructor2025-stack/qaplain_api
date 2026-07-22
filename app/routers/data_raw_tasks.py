import os
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException
from google.cloud import firestore

from app.core.cloud_tasks import (
    CLOUD_TASKS_AUDIENCE,
    CLOUD_TASKS_WORKER_URL,
    authenticate_cloud_task,
    create_http_task,
    ensure_task_queue,
)
from app.core.firebase import (
    get_firestore_client,
)
from app.routers.data_import_common import (
    normalize_text,
    now_iso,
)
from app.routers.data_raw_processor import (
    DATA_IMPORT_COLLECTION,
    UPLOADED_FILE_COLLECTION,
    process_source_file,
)


DATA_RAW_BATCH_COLLECTION = (
    "data_raw_batches"
)

DATA_RAW_QUEUE_PREFIX = os.getenv(
    "DATA_RAW_QUEUE_PREFIX",
    "data-raw",
)

DATA_SOURCE_COLLECTION = "data_sources"
TENANT_COLLECTION = "tenants"

DEFAULT_TASK_CONCURRENCY = 1
DEFAULT_TASK_MAX_ATTEMPTS = 5


def get_data_raw_worker_url() -> str:
    configured_url = normalize_text(
        CLOUD_TASKS_WORKER_URL
    )

    if not configured_url:
        raise HTTPException(
            status_code=500,
            detail=(
                "CLOUD_TASKS_WORKER_URLが"
                "設定されていません。"
            ),
        )

    parsed = urlsplit(
        configured_url
    )

    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(
            status_code=500,
            detail=(
                "CLOUD_TASKS_WORKER_URLの"
                "形式が正しくありません。"
            ),
        )

    base_url = (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
    )

    return (
        f"{base_url}"
        "/data-raw/tasks/worker"
    )


def get_task_settings(
    data_source_id: str,
) -> dict:
    db = get_firestore_client()

    data_source_document = (
        db.collection(
            DATA_SOURCE_COLLECTION
        )
        .document(
            data_source_id
        )
        .get()
    )

    if not data_source_document.exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "データソースが"
                "見つかりません。"
            ),
        )

    data_source = (
        data_source_document.to_dict()
        or {}
    )

    tenant_id = normalize_text(
        data_source.get(
            "tenant_id"
        )
    )

    if not tenant_id:
        return {
            "tenant_id": "",
            "task_concurrency":
                DEFAULT_TASK_CONCURRENCY,
            "task_max_attempts":
                DEFAULT_TASK_MAX_ATTEMPTS,
        }

    tenant_document = (
        db.collection(
            TENANT_COLLECTION
        )
        .document(
            tenant_id
        )
        .get()
    )

    if not tenant_document.exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "データソースに設定された"
                "テナントが見つかりません。"
            ),
        )

    tenant = (
        tenant_document.to_dict()
        or {}
    )

    return {
        "tenant_id":
            tenant_id,
        "task_concurrency":
            tenant.get(
                "task_concurrency",
                DEFAULT_TASK_CONCURRENCY,
            ),
        "task_max_attempts":
            tenant.get(
                "task_max_attempts",
                DEFAULT_TASK_MAX_ATTEMPTS,
            ),
    }


def get_data_raw_queue(
    data_source_id: str,
) -> dict:
    normalized_data_source_id = (
        normalize_text(
            data_source_id
        )
    )

    if not normalized_data_source_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "データソースIDが"
                "指定されていません。"
            ),
        )

    task_settings = get_task_settings(
        normalized_data_source_id
    )

    try:
        return ensure_task_queue(
            identifier=
                normalized_data_source_id,
            concurrency=
                task_settings[
                    "task_concurrency"
                ],
            max_attempts=
                task_settings[
                    "task_max_attempts"
                ],
            prefix=
                DATA_RAW_QUEUE_PREFIX,
        )

    except Exception as error:
        print(
            "Data Raw queue setup error: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "データ解析用Cloud Tasks"
                "キューを準備できませんでした。"
            ),
        )


def enqueue_data_raw_task(
    *,
    queue_full_name: str,
    payload: dict,
):
    worker_url = (
        get_data_raw_worker_url()
    )

    # 認証側は既存Cloud Tasks設定と
    # 同じAudienceを使用する。
    audience = normalize_text(
        CLOUD_TASKS_AUDIENCE
        or CLOUD_TASKS_WORKER_URL
    )

    try:
        return create_http_task(
            queue_full_name=
                queue_full_name,
            payload=
                payload,
            worker_url=
                worker_url,
            audience=
                audience,
        )

    except Exception as error:
        print(
            "Data Raw task enqueue error: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "データ解析タスクを"
                "登録できませんでした。"
            ),
        )


def create_batch(
    *,
    data_source_id: str,
    user: dict,
) -> dict:
    normalized_data_source_id = (
        normalize_text(
            data_source_id
        )
    )

    if not normalized_data_source_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "データソースを"
                "選択してください。"
            ),
        )

    queue_config = (
        get_data_raw_queue(
            normalized_data_source_id
        )
    )

    batch_id = uuid.uuid4().hex
    now = now_iso()

    data = {
        "batch_id":
            batch_id,
        "data_source_id":
            normalized_data_source_id,
        "queue_id":
            queue_config["queue_id"],
        "task_concurrency":
            queue_config[
                "task_concurrency"
            ],
        "task_max_attempts":
            queue_config[
                "task_max_attempts"
            ],
        "status":
            "queued",
        "total_count":
            0,
        "queued_count":
            0,
        "completed_count":
            0,
        "failed_count":
            0,
        "created_at":
            now,
        "created_by":
            user.get("email", ""),
        "updated_at":
            now,
    }

    reference = (
        get_firestore_client()
        .collection(
            DATA_RAW_BATCH_COLLECTION
        )
        .document(batch_id)
    )

    reference.set(data)

    try:
        task = enqueue_data_raw_task(
            queue_full_name=
                queue_config[
                    "queue_full_name"
                ],
            payload={
                "task_type":
                    "data_raw_dispatch",
                "batch_id":
                    batch_id,
            },
        )

        reference.set({
            "dispatch_task_name":
                task.name,
            "updated_at":
                now_iso(),
        }, merge=True)

    except Exception:
        reference.set({
            "status":
                "failed",
            "error_message":
                (
                    "一括解析タスクを"
                    "登録できませんでした。"
                ),
            "updated_at":
                now_iso(),
        }, merge=True)

        raise

    return {
        "status":
            "queued",
        "batch_id":
            batch_id,
        "queue_id":
            queue_config["queue_id"],
        "task_concurrency":
            queue_config[
                "task_concurrency"
            ],
        "task_max_attempts":
            queue_config[
                "task_max_attempts"
            ],
        "message":
            "一括解析を受け付けました。",
    }


def reset_analysis_state(
    *,
    data_source_id: str,
    user: dict,
) -> dict:
    normalized_data_source_id = (
        normalize_text(
            data_source_id
        )
    )

    if not normalized_data_source_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "データソースを"
                "選択してください。"
            ),
        )

    db = get_firestore_client()

    reset_source_count = 0

    for source in iter_source_documents(
        normalized_data_source_id
    ):
        source_reference = (
            db.collection(
                source["collection_name"]
            )
            .document(
                source["source_id"]
            )
        )

        source_reference.set({
            "analysis_status":
                firestore.DELETE_FIELD,
            "analysis_batch_id":
                firestore.DELETE_FIELD,
            "analysis_task_name":
                firestore.DELETE_FIELD,
            "analysis_error":
                firestore.DELETE_FIELD,
            "analysis_error_message":
                firestore.DELETE_FIELD,
            "analysis_started_at":
                firestore.DELETE_FIELD,
            "analysis_completed_at":
                firestore.DELETE_FIELD,
            "updated_at":
                now_iso(),
        }, merge=True)

        reset_source_count += 1

    batch_documents = list(
        db.collection(
            DATA_RAW_BATCH_COLLECTION
        )
        .where(
            "data_source_id",
            "==",
            normalized_data_source_id,
        )
        .stream()
    )

    deleted_batch_count = 0

    for offset in range(
        0,
        len(batch_documents),
        400,
    ):
        write_batch = db.batch()
        chunk = batch_documents[
            offset:offset + 400
        ]

        for document in chunk:
            write_batch.delete(
                document.reference
            )

        if chunk:
            write_batch.commit()
            deleted_batch_count += (
                len(chunk)
            )

    return {
        "status":
            "reset",
        "data_source_id":
            normalized_data_source_id,
        "reset_source_count":
            reset_source_count,
        "deleted_batch_count":
            deleted_batch_count,
        "reset_by":
            user.get("email", ""),
        "message":
            "解析状態をリセットしました。",
    }


def iter_source_documents(
    data_source_id: str,
):
    db = get_firestore_client()

    definitions = (
        (
            "uploaded_file",
            UPLOADED_FILE_COLLECTION,
        ),
        (
            "api_import",
            DATA_IMPORT_COLLECTION,
        ),
    )

    for (
        source_type,
        collection_name,
    ) in definitions:

        documents = (
            db.collection(
                collection_name
            )
            .where(
                "data_source_id",
                "==",
                data_source_id,
            )
            .stream()
        )

        for document in documents:
            data = (
                document.to_dict()
                or {}
            )

            if data.get(
                "deleted",
                False,
            ):
                continue

            yield {
                "source_type":
                    source_type,
                "source_id":
                    document.id,
                "collection_name":
                    collection_name,
                "data":
                    data,
            }


def dispatch_batch(
    *,
    batch_id: str,
) -> dict:
    db = get_firestore_client()

    batch_reference = (
        db.collection(
            DATA_RAW_BATCH_COLLECTION
        )
        .document(batch_id)
    )

    batch_document = (
        batch_reference.get()
    )

    if not batch_document.exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "一括解析ジョブが"
                "見つかりません。"
            ),
        )

    batch_data = (
        batch_document.to_dict()
        or {}
    )

    if batch_data.get(
        "status"
    ) in {
        "dispatching",
        "running",
        "completed",
        "completed_with_errors",
    }:
        return {
            "status":
                "already_dispatched",
            "batch_id":
                batch_id,
        }

    data_source_id = normalize_text(
        batch_data.get(
            "data_source_id"
        )
    )

    queue_config = (
        get_data_raw_queue(
            data_source_id
        )
    )

    batch_reference.set({
        "status":
            "dispatching",
        "started_at":
            batch_data.get(
                "started_at"
            )
            or now_iso(),
        "updated_at":
            now_iso(),
    }, merge=True)

    sources = list(
        iter_source_documents(
            data_source_id
        )
    )

    pending_sources = [
        source
        for source in sources
        if normalize_text(
            source["data"].get(
                "analysis_status"
            )
        ).lower()
        != "completed"
    ]

    total_count = len(
        pending_sources
    )

    batch_reference.set({
        "total_count":
            total_count,
        "updated_at":
            now_iso(),
    }, merge=True)

    if total_count == 0:
        batch_reference.set({
            "status":
                "completed",
            "completed_at":
                now_iso(),
            "updated_at":
                now_iso(),
        }, merge=True)

        return {
            "status":
                "completed",
            "batch_id":
                batch_id,
            "queued_count":
                0,
        }

    queued_count = 0
    enqueue_failed_count = 0

    for source in pending_sources:
        try:
            task = enqueue_data_raw_task(
                queue_full_name=
                    queue_config[
                        "queue_full_name"
                    ],
                payload={
                    "task_type":
                        "data_raw_process",
                    "batch_id":
                        batch_id,
                    "source_type":
                        source[
                            "source_type"
                        ],
                    "source_id":
                        source[
                            "source_id"
                        ],
                },
            )

            queued_count += 1

            (
                db.collection(
                    source["collection_name"]
                )
                .document(
                    source["source_id"]
                )
                .set({
                    "analysis_status":
                        "queued",
                    "analysis_batch_id":
                        batch_id,
                    "analysis_task_name":
                        task.name,
                    "updated_at":
                        now_iso(),
                }, merge=True)
            )

        except Exception as error:
            enqueue_failed_count += 1

            print(
                "Data Raw item enqueue error: "
                f"{source['source_id']} "
                f"{type(error).__name__}: "
                f"{error}"
            )

        if (
            queued_count
            + enqueue_failed_count
        ) % 25 == 0:
            batch_reference.set({
                "queued_count":
                    queued_count,
                "failed_count":
                    enqueue_failed_count,
                "updated_at":
                    now_iso(),
            }, merge=True)

    status = (
        "running"
        if queued_count > 0
        else "failed"
    )

    batch_reference.set({
        "status":
            status,
        "queued_count":
            queued_count,
        "failed_count":
            enqueue_failed_count,
        "updated_at":
            now_iso(),
    }, merge=True)

    return {
        "status":
            status,
        "batch_id":
            batch_id,
        "total_count":
            total_count,
        "queued_count":
            queued_count,
        "failed_count":
            enqueue_failed_count,
    }


@firestore.transactional
def update_batch_result(
    transaction,
    batch_reference,
    *,
    success: bool,
) -> dict:
    snapshot = batch_reference.get(
        transaction=transaction
    )

    data = (
        snapshot.to_dict()
        or {}
    )

    completed_count = int(
        data.get(
            "completed_count",
            0,
        )
        or 0
    )

    failed_count = int(
        data.get(
            "failed_count",
            0,
        )
        or 0
    )

    if success:
        completed_count += 1
    else:
        failed_count += 1

    total_count = int(
        data.get(
            "total_count",
            0,
        )
        or 0
    )

    finished_count = (
        completed_count
        + failed_count
    )

    values = {
        "completed_count":
            completed_count,
        "failed_count":
            failed_count,
        "updated_at":
            now_iso(),
    }

    if (
        total_count > 0
        and finished_count >= total_count
    ):
        values.update({
            "status": (
                "completed_with_errors"
                if failed_count > 0
                else "completed"
            ),
            "completed_at":
                now_iso(),
        })

    transaction.set(
        batch_reference,
        values,
        merge=True,
    )

    return values


def process_batch_item(
    *,
    batch_id: str,
    source_type: str,
    source_id: str,
) -> dict:
    db = get_firestore_client()

    batch_reference = (
        db.collection(
            DATA_RAW_BATCH_COLLECTION
        )
        .document(batch_id)
    )

    try:
        result = process_source_file(
            source_type=
                source_type,
            source_id=
                source_id,
            overwrite=
                True,
            user={
                "email":
                    "cloud-tasks",
            },
        )

        transaction = db.transaction()

        update_batch_result(
            transaction,
            batch_reference,
            success=True,
        )

        return result

    except Exception:
        transaction = db.transaction()

        update_batch_result(
            transaction,
            batch_reference,
            success=False,
        )

        raise


def execute_data_raw_worker(
    *,
    request,
    authorization: str,
) -> dict:
    authenticate_cloud_task(
        authorization
    )

    task_type = normalize_text(
        request.task_type
    )

    if task_type == "data_raw_dispatch":
        return dispatch_batch(
            batch_id=
                request.batch_id,
        )

    if task_type == "data_raw_process":
        return process_batch_item(
            batch_id=
                request.batch_id,
            source_type=
                request.source_type,
            source_id=
                request.source_id,
        )

    raise HTTPException(
        status_code=400,
        detail=(
            "未対応のデータ解析"
            f"タスクです: {task_type}"
        ),
    )


def get_analysis_summary(
    data_source_id: str,
) -> dict:
    normalized_data_source_id = (
        normalize_text(
            data_source_id
        )
    )

    total_count = 0
    completed_count = 0
    failed_count = 0
    running_count = 0

    for source in iter_source_documents(
        normalized_data_source_id
    ):
        total_count += 1

        status = normalize_text(
            source["data"].get(
                "analysis_status"
            )
        ).lower()

        if status == "completed":
            completed_count += 1

        elif status == "failed":
            failed_count += 1

        elif status in {
            "queued",
            "running",
        }:
            running_count += 1

    pending_count = max(
        total_count
        - completed_count
        - failed_count
        - running_count,
        0,
    )

    batches = (
        get_firestore_client()
        .collection(
            DATA_RAW_BATCH_COLLECTION
        )
        .where(
            "data_source_id",
            "==",
            normalized_data_source_id,
        )
        .stream()
    )

    values = []

    for document in batches:
        data = (
            document.to_dict()
            or {}
        )
        data["batch_id"] = document.id
        values.append(data)

    values.sort(
        key=lambda item:
            normalize_text(
                item.get(
                    "created_at"
                )
            ),
        reverse=True,
    )

    return {
        "data_source_id":
            normalized_data_source_id,
        "total_count":
            total_count,
        "completed_count":
            completed_count,
        "pending_count":
            pending_count,
        "running_count":
            running_count,
        "failed_count":
            failed_count,
        "latest_batch": (
            values[0]
            if values
            else None
        ),
    }
