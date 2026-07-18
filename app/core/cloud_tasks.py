import hashlib
import json
import os
from typing import Any

from fastapi import HTTPException
from google.auth.transport import requests as google_auth_requests
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import tasks_v2
from google.oauth2 import id_token as google_id_token
from google.protobuf.field_mask_pb2 import FieldMask


CLOUD_TASKS_PROJECT = os.getenv(
    "CLOUD_TASKS_PROJECT",
    os.getenv("GOOGLE_CLOUD_PROJECT", ""),
)

CLOUD_TASKS_LOCATION = os.getenv(
    "CLOUD_TASKS_LOCATION",
    "asia-northeast1",
)

CLOUD_TASKS_WORKER_URL = os.getenv(
    "CLOUD_TASKS_WORKER_URL",
    "",
)

CLOUD_TASKS_SERVICE_ACCOUNT = os.getenv(
    "CLOUD_TASKS_SERVICE_ACCOUNT",
    "",
)

CLOUD_TASKS_AUDIENCE = os.getenv(
    "CLOUD_TASKS_AUDIENCE",
    "",
)

DEFAULT_QUEUE_PREFIX = "data-import"
MIN_TASK_CONCURRENCY = 1
MAX_TASK_CONCURRENCY = 10


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_email(value: Any) -> str:
    return normalize_text(value).lower()


def normalize_task_concurrency(value: Any) -> int:
    try:
        concurrency = int(value)
    except (TypeError, ValueError):
        concurrency = MIN_TASK_CONCURRENCY

    return max(
        MIN_TASK_CONCURRENCY,
        min(concurrency, MAX_TASK_CONCURRENCY),
    )


def build_task_queue_id(
    identifier: str,
    prefix: str = DEFAULT_QUEUE_PREFIX,
) -> str:
    normalized_identifier = normalize_text(identifier)
    normalized_prefix = (
        normalize_text(prefix)
        .lower()
        .replace("_", "-")
    )

    if not normalized_identifier:
        raise ValueError(
            "キュー識別子が指定されていません。"
        )

    if not normalized_prefix:
        raise ValueError(
            "キュー接頭辞が指定されていません。"
        )

    digest = hashlib.sha256(
        normalized_identifier.encode("utf-8")
    ).hexdigest()[:32]

    return f"{normalized_prefix}-{digest}"


def get_cloud_tasks_client() -> tasks_v2.CloudTasksClient:
    return tasks_v2.CloudTasksClient()


def validate_cloud_tasks_settings() -> None:
    if not CLOUD_TASKS_PROJECT:
        raise RuntimeError(
            "CLOUD_TASKS_PROJECTが設定されていません。"
        )

    if not CLOUD_TASKS_WORKER_URL:
        raise RuntimeError(
            "CLOUD_TASKS_WORKER_URLが設定されていません。"
        )

    if not CLOUD_TASKS_SERVICE_ACCOUNT:
        raise RuntimeError(
            "CLOUD_TASKS_SERVICE_ACCOUNTが設定されていません。"
        )


def get_task_queue_full_name(
    identifier: str,
    prefix: str = DEFAULT_QUEUE_PREFIX,
) -> str:
    if not CLOUD_TASKS_PROJECT:
        raise RuntimeError(
            "CLOUD_TASKS_PROJECTが設定されていません。"
        )

    queue_id = build_task_queue_id(
        identifier=identifier,
        prefix=prefix,
    )

    return get_cloud_tasks_client().queue_path(
        CLOUD_TASKS_PROJECT,
        CLOUD_TASKS_LOCATION,
        queue_id,
    )


def ensure_task_queue(
    *,
    identifier: str,
    concurrency: int,
    prefix: str = DEFAULT_QUEUE_PREFIX,
) -> dict:
    if not CLOUD_TASKS_PROJECT:
        raise RuntimeError(
            "CLOUD_TASKS_PROJECTが設定されていません。"
        )

    normalized_concurrency = (
        normalize_task_concurrency(concurrency)
    )
    queue_id = build_task_queue_id(
        identifier=identifier,
        prefix=prefix,
    )
    client = get_cloud_tasks_client()
    queue_full_name = client.queue_path(
        CLOUD_TASKS_PROJECT,
        CLOUD_TASKS_LOCATION,
        queue_id,
    )
    parent = client.common_location_path(
        CLOUD_TASKS_PROJECT,
        CLOUD_TASKS_LOCATION,
    )

    queue = tasks_v2.Queue(
        name=queue_full_name,
        rate_limits=tasks_v2.RateLimits(
            max_concurrent_dispatches=(
                normalized_concurrency
            ),
            max_dispatches_per_second=float(
                normalized_concurrency
            ),
        ),
    )

    try:
        client.get_queue(
            request={
                "name": queue_full_name,
            }
        )

    except NotFound:
        try:
            client.create_queue(
                request={
                    "parent": parent,
                    "queue": queue,
                }
            )

        except AlreadyExists:
            pass

    updated_queue = client.update_queue(
        request={
            "queue": queue,
            "update_mask": FieldMask(
                paths=[
                    (
                        "rate_limits."
                        "max_concurrent_dispatches"
                    ),
                    (
                        "rate_limits."
                        "max_dispatches_per_second"
                    ),
                ]
            ),
        }
    )

    return {
        "queue_id": queue_id,
        "queue_full_name": updated_queue.name,
        "task_concurrency": normalized_concurrency,
    }


def create_http_task(
    *,
    queue_full_name: str,
    payload: dict,
    worker_url: str | None = None,
    service_account_email: str | None = None,
    audience: str | None = None,
) -> tasks_v2.Task:
    validate_cloud_tasks_settings()

    target_url = normalize_text(
        worker_url or CLOUD_TASKS_WORKER_URL
    )
    service_account = normalize_text(
        service_account_email
        or CLOUD_TASKS_SERVICE_ACCOUNT
    )
    target_audience = normalize_text(
        audience
        or CLOUD_TASKS_AUDIENCE
        or target_url
    )

    body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    task = tasks_v2.Task(
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=target_url,
            headers={
                "Content-Type": "application/json",
            },
            oidc_token=tasks_v2.OidcToken(
                service_account_email=service_account,
                audience=target_audience,
            ),
            body=body,
        )
    )

    return get_cloud_tasks_client().create_task(
        request={
            "parent": queue_full_name,
            "task": task,
        }
    )


def authenticate_cloud_task(
    authorization: str,
) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Cloud Tasks認証情報がありません。",
        )

    token = authorization.replace(
        "Bearer ",
        "",
        1,
    ).strip()
    audience = normalize_text(
        CLOUD_TASKS_AUDIENCE
        or CLOUD_TASKS_WORKER_URL
    )

    if not audience:
        raise HTTPException(
            status_code=500,
            detail=(
                "CLOUD_TASKS_AUDIENCEまたは"
                "CLOUD_TASKS_WORKER_URLが未設定です。"
            ),
        )

    try:
        decoded_token = (
            google_id_token.verify_oauth2_token(
                token,
                google_auth_requests.Request(),
                audience,
            )
        )
    except Exception as error:
        print(
            "Cloud Tasks OIDC verification error: "
            f"{type(error).__name__}: {error}"
        )
        raise HTTPException(
            status_code=401,
            detail="Cloud Tasks認証情報を確認できませんでした。",
        )

    expected_email = normalize_email(
        CLOUD_TASKS_SERVICE_ACCOUNT
    )
    actual_email = normalize_email(
        decoded_token.get("email", "")
    )

    if (
        expected_email
        and actual_email != expected_email
    ):
        raise HTTPException(
            status_code=403,
            detail="Cloud Tasks実行アカウントが一致しません。",
        )

    return decoded_token
