import json
import os
import uuid
from typing import Any

from fastapi import HTTPException
from google.cloud import firestore
from google.cloud import tasks_v2

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
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

TASK_PROJECT_ID = os.getenv(
    "GOOGLE_CLOUD_PROJECT",
    os.getenv(
        "GCP_PROJECT",
        "",
    ),
)

TASK_LOCATION = os.getenv(
    "TASK_LOCATION",
    "asia-northeast1",
)

TASK_QUEUE = os.getenv(
    "DATA_RAW_TASK_QUEUE",
    "data-raw",
)

SERVICE_BASE_URL = os.getenv(
    "SERVICE_BASE_URL",
    "",
).rstrip("/")

TASK_SERVICE_ACCOUNT = os.getenv(
    "TASK_SERVICE_ACCOUNT",
    "",
)

TASK_AUDIENCE = os.getenv(
    "TASK_AUDIENCE",
    SERVICE_BASE_URL,
)

TASK_SERVICE_ACCOUNT_EMAILS = {
    normalize_text(value).lower()
    for value in os.getenv(
        "TASK_SERVICE_ACCOUNT_EMAILS",
        TASK_SERVICE_ACCOUNT,
    ).split(",")
    if normalize_text(value)
}


def authenticate_task(
    authorization: str,
) -> dict:
    if not authorization.startswith(
        "Bearer "
    ):
        raise HTTPException(
            status_code=401,
            detail="Cloud Tasks認証情報がありません。",
        )

    token = authorization.replace(
        "Bearer ",
        "",
        1,
    ).strip()

    try:
        decoded = verify_id_token(token)
    except Exception as error:
        print(
            "Cloud Tasks token error: "
            f"{type(error).__name__}: {error}"
        )
        raise HTTPException(
            status_code=401,
            detail="Cloud Tasks認証を確認できません。",
        )

    email = normalize_text(
        decoded.get("email")
    ).lower()

    if (
        TASK_SERVICE_ACCOUNT_EMAILS
        and email
        not in TASK_SERVICE_ACCOUNT_EMAILS
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "許可されていない"
                "Cloud Tasks実行ユーザーです。"
            ),
        )

    return decoded


def validate_task_settings() -> None:
    missing = []

    if not TASK_PROJECT_ID:
        missing.append(
            "GOOGLE_CLOUD_PROJECT"
        )

    if not TASK_LOCATION:
        missing.append(
            "TASK_LOCATION"
        )

    if not TASK_QUEUE:
        missing.append(
            "DATA_RAW_TASK_QUEUE"
        )

    if not SERVICE_BASE_URL:
        missing.append(
            "SERVICE_BASE_URL"
        )

    if not TASK_SERVICE_ACCOUNT:
        missing.append(
            "TASK_SERVICE_ACCOUNT"
        )

    if missing:
        raise HTTPException(
            status_code=500,
            detail=(
                "Cloud Tasks設定が不足しています: "
                + ", ".join(missing)
            ),
        )


def create_http_task(
    *,
    path: str,
    payload: dict,
) -> str:
    validate_task_settings()

    client = tasks_v2.CloudTasksClient()

    parent = client.queue_path(
        TASK_PROJECT_ID,
        TASK_LOCATION,
        TASK_QUEUE,
    )

    body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    task = tasks_v2.Task(
        http_request=
            tasks_v2.HttpRequest(
                http_method=
                    tasks_v2.HttpMethod.POST,
                url=(
                    f"{SERVICE_BASE_URL}"
                    f"{path}"
                ),
                headers={
                    "Content-Type":
                        "application/json",
                },
                oidc_token=
                    tasks_v2.OidcToken(
                        service_account_email=
                            TASK_SERVICE_ACCOUNT,
                        audience=(
                            TASK_AUDIENCE
                            or SERVICE_BASE_URL
                        ),
                    ),
                body=body,
            ),
    )

    response = client.create_task(
        request={
            "parent":
                parent,
            "task":
                task,
        }
    )

    return response.name


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

    batch_id = uuid.uuid4().hex
    now = now_iso()

    data = {
        "batch_id":
            batch_id,
        "data_source_id":
            normalized_data_source_id,
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
        "skipped_count":
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
        task_name = create_http_task(
            path=(
                "/v1/data-raw/tasks/"
                "dispatch"
            ),
            payload={
                "batch_id":
                    batch_id,
            },
        )

        reference.set({
            "dispatch_task_name":
                task_name,
            "updated_at":
                now_iso(),
        }, merge=True)

    except Exception:
        reference.set({
            "status":
                "failed",
            "error_message":
                "一括解析タスクを登録できませんでした。",
            "updated_at":
                now_iso(),
        }, merge=True)

        raise

    return {
        "status":
            "queued",
        "batch_id":
            batch_id,
        "message":
            "一括解析を受け付けました。",
    }


def _iter_source_documents(
    data_source_id: str,
):
    db = get_firestore_client()

    collection_definitions = (
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
    ) in collection_definitions:

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
            data = document.to_dict() or {}

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
        _iter_source_documents(
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
    failed_to_queue_count = 0

    for source in pending_sources:
        try:
            create_http_task(
                path=(
                    "/v1/data-raw/tasks/"
                    "process"
                ),
                payload={
                    "batch_id":
                        batch_id,
                    "source_type":
                        source["source_type"],
                    "source_id":
                        source["source_id"],
                },
            )

            queued_count += 1

        except Exception as error:
            failed_to_queue_count += 1

            print(
                "data raw task enqueue error: "
                f"{source['source_id']} "
                f"{type(error).__name__}: "
                f"{error}"
            )

        if (
            queued_count
            + failed_to_queue_count
        ) % 25 == 0:
            batch_reference.set({
                "queued_count":
                    queued_count,
                "failed_count":
                    failed_to_queue_count,
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
            failed_to_queue_count,
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
            failed_to_queue_count,
    }


@firestore.transactional
def _update_batch_result(
    transaction,
    batch_reference,
    *,
    success: bool,
) -> dict:
    snapshot = batch_reference.get(
        transaction=transaction
    )

    data = snapshot.to_dict() or {}

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

    update_data = {
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
        update_data.update({
            "status":
                "completed_with_errors"
                if failed_count > 0
                else "completed",
            "completed_at":
                now_iso(),
        })

    transaction.set(
        batch_reference,
        update_data,
        merge=True,
    )

    return update_data


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

        _update_batch_result(
            transaction,
            batch_reference,
            success=True,
        )

        return result

    except Exception:
        transaction = db.transaction()

        _update_batch_result(
            transaction,
            batch_reference,
            success=False,
        )

        raise


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

    for source in _iter_source_documents(
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

    latest_batch = None

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

    batch_values = []

    for document in batches:
        data = document.to_dict() or {}
        data["batch_id"] = document.id
        batch_values.append(data)

    batch_values.sort(
        key=lambda item:
            normalize_text(
                item.get(
                    "created_at"
                )
            ),
        reverse=True,
    )

    if batch_values:
        latest_batch = batch_values[0]

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
        "latest_batch":
            latest_batch,
    }
