from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.core.cloud_tasks import create_http_task, ensure_task_queue, normalize_task_concurrency
from app.core.firebase import get_firestore_client
from app.routers.data_import_common import (
    DATA_IMPORT_TASK_COLLECTION,
    TENANT_COLLECTION,
    build_requested_url,
    normalize_content_type,
    normalize_key,
    normalize_text,
    now_iso,
    save_import_file,
)


def build_auth_headers(data_source: dict) -> dict[str, str]:
    method_key = normalize_key(data_source.get("authentication_method_key", ""))

    if method_key == "none":
        from app.routers.data_import_none import build_auth_headers as builder
    elif method_key == "basic":
        from app.routers.data_import_basic import build_auth_headers as builder
    elif method_key == "client_credentials":
        from app.routers.data_import_client_credentials import build_auth_headers as builder
    else:
        raise HTTPException(status_code=400, detail=f"未対応の認証方式です。 method={method_key}")

    return builder(data_source)


def request_external_data(data_source: dict) -> tuple[bytes, int, str, str]:
    requested_url = build_requested_url(data_source)
    headers = {
        "User-Agent": "QAPlain-Knowledge-Studio/1.0",
        "Accept": (
            "application/json, application/xml, text/xml, text/csv, "
            "text/plain, application/pdf, application/zip, */*;q=0.8"
        ),
        **build_auth_headers(data_source),
    }
    request = Request(requested_url, method="GET", headers=headers)

    try:
        with urlopen(request, timeout=60) as response:
            content = response.read()
            http_status = int(response.status)
            content_type = normalize_content_type(response.headers.get("Content-Type"))
    except HTTPError as error:
        external_detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise HTTPException(
            status_code=502,
            detail={
                "message": "接続先APIからエラーが返されました。",
                "external_status": error.code,
                "external_detail": external_detail,
            },
        )
    except URLError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "接続先APIへ接続できませんでした。",
                "external_detail": str(error.reason),
            },
        )
    except HTTPException:
        raise
    except Exception as error:
        print(f"external request error: {type(error).__name__}: {error}")
        raise HTTPException(status_code=502, detail="外部データの取得に失敗しました。")

    if http_status < 200 or http_status >= 300:
        raise HTTPException(status_code=502, detail="接続先APIから正常でない応答が返されました。")
    if content_type == "text/html":
        raise HTTPException(
            status_code=502,
            detail="接続先APIがHTMLを返しました。URLまたは接続条件を確認してください。",
        )

    return content, http_status, content_type, requested_url


def execute_import(*, data_source: dict, user: dict) -> dict:
    content, http_status, content_type, requested_url = request_external_data(data_source)
    return save_import_file(
        content=content,
        content_type=content_type,
        data_source=data_source,
        import_method=normalize_key(data_source.get("authentication_method_key", "")),
        user=user,
        source_url=requested_url,
        http_status=http_status,
    )


def get_tenant_task_queue_config(data_source: dict) -> dict:
    tenant_id = normalize_text(data_source.get("tenant_id", ""))
    if not tenant_id:
        raise HTTPException(status_code=400, detail="データソースにテナントIDが設定されていません。")

    tenant_document = get_firestore_client().collection(TENANT_COLLECTION).document(tenant_id).get()
    if not tenant_document.exists:
        raise HTTPException(status_code=404, detail="テナントが見つかりません。")

    tenant_data = tenant_document.to_dict() or {}
    task_concurrency = normalize_task_concurrency(tenant_data.get("task_concurrency", 1))
    try:
        queue_config = ensure_task_queue(identifier=tenant_id, concurrency=task_concurrency)
    except Exception as error:
        print(f"Cloud Tasks queue setup error: {type(error).__name__}: {error}")
        raise HTTPException(status_code=500, detail="テナント用Cloud Tasksキューを準備できませんでした。")

    return {"tenant_id": tenant_id, **queue_config}


def create_import_task_document(*, data_source: dict, user: dict, queue_id: str, task_concurrency: int) -> dict:
    import uuid

    task_id = uuid.uuid4().hex
    now = now_iso()
    data = {
        "task_id": task_id,
        "data_source_id": data_source["data_source_id"],
        "data_source_name": data_source.get("data_source_name", ""),
        "tenant_id": data_source.get("tenant_id", ""),
        "authentication_method_key": normalize_key(data_source.get("authentication_method_key", "")),
        "queue_id": queue_id,
        "task_concurrency": task_concurrency,
        "status": "queued",
        "result_item_id": None,
        "error_message": None,
        "requested_by": user.get("email", ""),
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "updated_at": now,
    }
    get_firestore_client().collection(DATA_IMPORT_TASK_COLLECTION).document(task_id).set(data)
    return data


def update_import_task(task_id: str, **values) -> None:
    values["updated_at"] = now_iso()
    get_firestore_client().collection(DATA_IMPORT_TASK_COLLECTION).document(task_id).set(values, merge=True)


def get_import_task(task_id: str) -> dict:
    document = get_firestore_client().collection(DATA_IMPORT_TASK_COLLECTION).document(task_id).get()
    if not document.exists:
        raise HTTPException(status_code=404, detail="取込タスクが見つかりません。")
    return {**(document.to_dict() or {}), "task_id": document.id}


def enqueue_import_task(*, data_source: dict, user: dict) -> dict:
    queue_config = get_tenant_task_queue_config(data_source)
    task_data = create_import_task_document(
        data_source=data_source,
        user=user,
        queue_id=queue_config["queue_id"],
        task_concurrency=queue_config["task_concurrency"],
    )

    try:
        response = create_http_task(
            queue_full_name=queue_config["queue_full_name"],
            payload={"task_id": task_data["task_id"]},
        )
    except Exception as error:
        update_import_task(
            task_data["task_id"],
            status="enqueue_failed",
            error_message=str(error),
            completed_at=now_iso(),
        )
        print(f"Cloud Tasks enqueue error: {type(error).__name__}: {error}")
        raise HTTPException(status_code=500, detail="データ取得タスクを登録できませんでした。")

    update_import_task(
        task_data["task_id"],
        cloud_task_name=response.name,
        queue_full_name=queue_config["queue_full_name"],
    )
    return {
        "message": "データ取得を受け付けました。",
        "task_id": task_data["task_id"],
        "data_source_id": data_source["data_source_id"],
        "tenant_id": queue_config["tenant_id"],
        "queue_id": queue_config["queue_id"],
        "task_concurrency": queue_config["task_concurrency"],
        "status": "queued",
    }
