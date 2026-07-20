import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.core.cloud_tasks import (
    create_http_task,
    ensure_task_queue,
    normalize_task_concurrency,
)
from app.core.firebase import get_firestore_client
from app.routers.data_import_common import (
    DATA_IMPORT_COLLECTION,
    DATA_IMPORT_TASK_COLLECTION,
    TENANT_COLLECTION,
    build_requested_url,
    get_storage_bucket,
    normalize_content_type,
    normalize_key,
    normalize_text,
    now_iso,
    save_downloaded_file,
    save_json_item,
    save_raw_response,
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
        raise HTTPException(
            status_code=400,
            detail=f"未対応の認証方式です。 method={method_key}",
        )

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
            content_type = normalize_content_type(
                response.headers.get("Content-Type")
            )
    except HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "接続先APIからエラーが返されました。",
                "external_status": error.code,
                "external_detail": error.read().decode(
                    "utf-8",
                    errors="replace",
                )[:1000],
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
        raise HTTPException(
            status_code=502,
            detail="接続先APIから正常でない応答が返されました。",
        )

    if content_type == "text/html":
        raise HTTPException(
            status_code=502,
            detail="接続先APIがHTMLを返しました。URLまたは接続条件を確認してください。",
        )

    return content, http_status, content_type, requested_url


def request_file(url: str, data_source: dict) -> tuple[bytes, int, str]:
    request = Request(
        url,
        method="GET",
        headers={
            "User-Agent": "QAPlain-Knowledge-Studio/1.0",
            "Accept": "*/*",
            **build_auth_headers(data_source),
        },
    )

    try:
        with urlopen(request, timeout=120) as response:
            return (
                response.read(),
                int(response.status),
                normalize_content_type(response.headers.get("Content-Type")),
            )
    except HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "ファイル取得先からエラーが返されました。",
                "external_status": error.code,
                "external_detail": error.read().decode(
                    "utf-8",
                    errors="replace",
                )[:1000],
            },
        )
    except URLError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "ファイル取得先へ接続できませんでした。",
                "external_detail": str(error.reason),
            },
        )


def get_path_value(data, path: str):
    current = data
    normalized_path = normalize_text(path)
    if not normalized_path:
        return current

    for part in normalized_path.split("."):
        part = part.strip()
        if not part:
            continue

        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
            continue

        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index < 0 or index >= len(current):
                return None
            current = current[index]
            continue

        return None

    return current


def get_path_values(data, path: str) -> list:
    """
    ドット区切りのパスをたどり、途中に配列がある場合は
    その全要素を展開して、終端の値を一覧で返す。

    例:
        result.results.resources

    は、result.results の全要素について resources を取得し、
    resources 配列の全要素を1つの一覧にまとめる。
    """
    normalized_path = normalize_text(path)
    if not normalized_path:
        return [data]

    parts = [
        part.strip()
        for part in normalized_path.split(".")
        if part.strip()
    ]

    current_values = [data]

    for part in parts:
        next_values = []

        for current in current_values:
            targets = current if isinstance(current, list) else [current]

            for target in targets:
                if isinstance(target, dict):
                    if part in target:
                        next_values.append(target[part])
                    continue

                if isinstance(target, list) and part.isdigit():
                    index = int(part)
                    if 0 <= index < len(target):
                        next_values.append(target[index])

        current_values = next_values

        if not current_values:
            return []

    flattened_values = []

    def flatten(value) -> None:
        if isinstance(value, list):
            for item in value:
                flatten(item)
            return

        flattened_values.append(value)

    for value in current_values:
        flatten(value)

    return flattened_values


def get_required_list(data, path: str, label: str) -> list:
    values = get_path_values(data, path)

    if not values:
        raise HTTPException(
            status_code=400,
            detail=f"{label}（{path}）を配列として取得できませんでした。",
        )

    return values


def get_tenant_task_queue_config(data_source: dict) -> dict:
    tenant_id = normalize_text(data_source.get("tenant_id", ""))
    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="データソースにテナントIDが設定されていません。",
        )

    tenant_document = (
        get_firestore_client()
        .collection(TENANT_COLLECTION)
        .document(tenant_id)
        .get()
    )
    if not tenant_document.exists:
        raise HTTPException(status_code=404, detail="テナントが見つかりません。")

    tenant_data = tenant_document.to_dict() or {}
    task_concurrency = normalize_task_concurrency(
        tenant_data.get("task_concurrency", 1)
    )

    try:
        queue_config = ensure_task_queue(
            identifier=tenant_id,
            concurrency=task_concurrency,
        )
    except Exception as error:
        print(f"Cloud Tasks queue setup error: {type(error).__name__}: {error}")
        raise HTTPException(
            status_code=500,
            detail="テナント用Cloud Tasksキューを準備できませんでした。",
        )

    return {"tenant_id": tenant_id, **queue_config}


def create_import_task_document(
    *,
    data_source: dict,
    user: dict,
    queue_id: str,
    task_concurrency: int,
    batch_id: str,
    task_type: str,
    payload: dict | None = None,
    parent_task_id: str | None = None,
) -> dict:
    task_id = uuid.uuid4().hex
    now = now_iso()
    data = {
        "task_id": task_id,
        "batch_id": batch_id,
        "task_type": task_type,
        "parent_task_id": parent_task_id,
        "payload": payload or {},
        "data_source_id": data_source["data_source_id"],
        "data_source_name": data_source.get("data_source_name", ""),
        "tenant_id": data_source.get("tenant_id", ""),
        "authentication_method_key": normalize_key(
            data_source.get("authentication_method_key", "")
        ),
        "processing_pattern": normalize_key(
            data_source.get("processing_pattern", "raw")
        ) or "raw",
        "queue_id": queue_id,
        "task_concurrency": task_concurrency,
        "status": "queued",
        "result_item_id": None,
        "result_item_count": 0,
        "error_message": None,
        "requested_by": user.get("email", ""),
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "updated_at": now,
    }
    (
        get_firestore_client()
        .collection(DATA_IMPORT_TASK_COLLECTION)
        .document(task_id)
        .set(data)
    )
    return data


def update_import_task(task_id: str, **values) -> None:
    values["updated_at"] = now_iso()
    (
        get_firestore_client()
        .collection(DATA_IMPORT_TASK_COLLECTION)
        .document(task_id)
        .set(values, merge=True)
    )


def get_import_task(task_id: str) -> dict:
    document = (
        get_firestore_client()
        .collection(DATA_IMPORT_TASK_COLLECTION)
        .document(task_id)
        .get()
    )
    if not document.exists:
        raise HTTPException(status_code=404, detail="取込タスクが見つかりません。")
    return {**(document.to_dict() or {}), "task_id": document.id}


def submit_task(queue_config: dict, task_data: dict) -> None:
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
        raise

    update_import_task(
        task_data["task_id"],
        cloud_task_name=response.name,
        queue_full_name=queue_config["queue_full_name"],
    )


ACTIVE_TASK_STATUSES = {"queued", "running"}


def get_active_import_tasks(data_source_id: str) -> list[dict]:
    documents = (
        get_firestore_client()
        .collection(DATA_IMPORT_TASK_COLLECTION)
        .where("data_source_id", "==", data_source_id)
        .stream()
    )

    return [
        {**(document.to_dict() or {}), "task_id": document.id}
        for document in documents
        if normalize_key((document.to_dict() or {}).get("status", ""))
        in ACTIVE_TASK_STATUSES
    ]


def delete_documents_by_data_source(
    collection_name: str,
    data_source_id: str,
) -> int:
    documents = list(
        get_firestore_client()
        .collection(collection_name)
        .where("data_source_id", "==", data_source_id)
        .stream()
    )

    deleted_count = 0
    for offset in range(0, len(documents), 400):
        batch = get_firestore_client().batch()
        chunk = documents[offset:offset + 400]

        for document in chunk:
            batch.delete(document.reference)

        if chunk:
            batch.commit()
            deleted_count += len(chunk)

    return deleted_count


def delete_storage_by_data_source(data_source_id: str) -> int:
    prefix = f"data-sources/{data_source_id}/imports/"
    deleted_count = 0

    try:
        for blob in get_storage_bucket().list_blobs(prefix=prefix):
            blob.delete()
            deleted_count += 1
    except Exception as error:
        print(
            "Cloud Storage cleanup error: "
            f"{type(error).__name__}: {error}"
        )
        raise HTTPException(
            status_code=500,
            detail="既存の取得ファイルを削除できませんでした。",
        )

    return deleted_count


def reset_import_data(data_source: dict) -> dict:
    data_source_id = data_source["data_source_id"]
    active_tasks = get_active_import_tasks(data_source_id)

    if active_tasks:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IMPORT_IN_PROGRESS",
                "message": "処理中の取込タスクがあるため再取込できません。",
                "active_task_count": len(active_tasks),
            },
        )

    deleted_storage_count = delete_storage_by_data_source(data_source_id)
    deleted_item_count = delete_documents_by_data_source(
        DATA_IMPORT_COLLECTION,
        data_source_id,
    )
    deleted_task_count = delete_documents_by_data_source(
        DATA_IMPORT_TASK_COLLECTION,
        data_source_id,
    )

    return {
        "deleted_storage_count": deleted_storage_count,
        "deleted_item_count": deleted_item_count,
        "deleted_task_count": deleted_task_count,
    }


def enqueue_import_task(*, data_source: dict, user: dict) -> dict:
    reset_result = reset_import_data(data_source)
    queue_config = get_tenant_task_queue_config(data_source)
    batch_id = uuid.uuid4().hex
    task_data = create_import_task_document(
        data_source=data_source,
        user=user,
        queue_id=queue_config["queue_id"],
        task_concurrency=queue_config["task_concurrency"],
        batch_id=batch_id,
        task_type="fetch_root",
    )

    try:
        submit_task(queue_config, task_data)
    except Exception as error:
        print(f"Cloud Tasks enqueue error: {type(error).__name__}: {error}")
        raise HTTPException(
            status_code=500,
            detail="データ取得タスクを登録できませんでした。",
        )

    return {
        "message": "データ取得を受け付けました。",
        "task_id": task_data["task_id"],
        "batch_id": batch_id,
        "data_source_id": data_source["data_source_id"],
        "tenant_id": queue_config["tenant_id"],
        "queue_id": queue_config["queue_id"],
        "task_concurrency": queue_config["task_concurrency"],
        "status": "queued",
        "reset_result": reset_result,
    }


def create_child_task(
    *,
    queue_config: dict,
    data_source: dict,
    user: dict,
    batch_id: str,
    parent_task_id: str,
    task_type: str,
    payload: dict,
) -> dict:
    task_data = create_import_task_document(
        data_source=data_source,
        user=user,
        queue_id=queue_config["queue_id"],
        task_concurrency=queue_config["task_concurrency"],
        batch_id=batch_id,
        task_type=task_type,
        payload=payload,
        parent_task_id=parent_task_id,
    )
    submit_task(queue_config, task_data)
    return task_data


def expand_json_list(
    *,
    root_json: dict | list,
    data_source: dict,
    root_task: dict,
    user: dict,
) -> int:
    items = get_required_list(
        root_json,
        data_source.get("list_array_path", ""),
        "一覧配列",
    )
    queue_config = get_tenant_task_queue_config(data_source)

    created = 0
    for index, item in enumerate(items):
        create_child_task(
            queue_config=queue_config,
            data_source=data_source,
            user=user,
            batch_id=root_task["batch_id"],
            parent_task_id=root_task["task_id"],
            task_type="save_json_item",
            payload={
                "data": item,
                "item_type": "list_item",
                "level": 1,
                "parent_id": None,
                "source_index": index,
            },
        )
        created += 1
    return created


def expand_parent_child(
    *,
    root_json: dict | list,
    data_source: dict,
    root_task: dict,
    user: dict,
    include_grandchildren: bool,
) -> int:
    parents = get_required_list(
        root_json,
        data_source.get("parent_array_path", ""),
        "親配列",
    )
    child_path = data_source.get("child_array_path", "")
    grandchild_path = data_source.get("grandchild_array_path", "")
    queue_config = get_tenant_task_queue_config(data_source)

    created = 0
    for parent_index, parent in enumerate(parents):
        parent_group_id = uuid.uuid4().hex
        create_child_task(
            queue_config=queue_config,
            data_source=data_source,
            user=user,
            batch_id=root_task["batch_id"],
            parent_task_id=root_task["task_id"],
            task_type="save_json_item",
            payload={
                "data": parent,
                "item_type": "parent",
                "level": 1,
                "parent_id": None,
                "fixed_item_id": parent_group_id,
                "source_index": parent_index,
            },
        )
        created += 1

        children = get_required_list(parent, child_path, "子配列")
        for child_index, child in enumerate(children):
            child_group_id = uuid.uuid4().hex
            create_child_task(
                queue_config=queue_config,
                data_source=data_source,
                user=user,
                batch_id=root_task["batch_id"],
                parent_task_id=root_task["task_id"],
                task_type="save_json_item",
                payload={
                    "data": child,
                    "item_type": "child",
                    "level": 2,
                    "parent_id": parent_group_id,
                    "fixed_item_id": child_group_id,
                    "source_index": child_index,
                },
            )
            created += 1

            if not include_grandchildren:
                continue

            grandchildren = get_required_list(child, grandchild_path, "孫配列")
            for grandchild_index, grandchild in enumerate(grandchildren):
                create_child_task(
                    queue_config=queue_config,
                    data_source=data_source,
                    user=user,
                    batch_id=root_task["batch_id"],
                    parent_task_id=root_task["task_id"],
                    task_type="save_json_item",
                    payload={
                        "data": grandchild,
                        "item_type": "grandchild",
                        "level": 3,
                        "parent_id": child_group_id,
                        "source_index": grandchild_index,
                    },
                )
                created += 1

    return created


def expand_file_links(
    *,
    root_json: dict | list,
    data_source: dict,
    root_task: dict,
    user: dict,
) -> int:
    items = get_required_list(
        root_json,
        data_source.get("file_link_array_path", ""),
        "一覧配列",
    )
    link_field = data_source.get("file_link_field_name", "")
    queue_config = get_tenant_task_queue_config(data_source)

    created = 0
    for index, item in enumerate(items):
        url = get_path_value(item, link_field)
        if not normalize_text(url):
            continue
        create_child_task(
            queue_config=queue_config,
            data_source=data_source,
            user=user,
            batch_id=root_task["batch_id"],
            parent_task_id=root_task["task_id"],
            task_type="download_file",
            payload={
                "url": normalize_text(url),
                "source_index": index,
                "parent_id": None,
                "source_metadata": item if isinstance(item, dict) else {},
            },
        )
        created += 1
    return created


def execute_root_task(*, data_source: dict, user: dict, task_data: dict) -> dict:
    content, http_status, content_type, requested_url = request_external_data(data_source)
    pattern = normalize_key(data_source.get("processing_pattern", "raw")) or "raw"

    raw_item = save_raw_response(
        content=content,
        content_type=content_type,
        data_source=data_source,
        user=user,
        source_url=requested_url,
        http_status=http_status,
        batch_id=task_data["batch_id"],
        task_id=task_data["task_id"],
    )

    if pattern == "raw":
        return {
            "item_id": raw_item["item_id"],
            "created_task_count": 0,
            "result_item_count": 1,
        }

    if content_type != "application/json":
        raise HTTPException(
            status_code=400,
            detail=f"処理方式{pattern}はJSONレスポンスでのみ利用できます。",
        )

    try:
        root_json = json.loads(content.decode("utf-8"))
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"JSONレスポンスを解析できませんでした。 {error}",
        )

    if pattern == "json_list":
        created = expand_json_list(
            root_json=root_json,
            data_source=data_source,
            root_task=task_data,
            user=user,
        )
    elif pattern == "parent_child":
        created = expand_parent_child(
            root_json=root_json,
            data_source=data_source,
            root_task=task_data,
            user=user,
            include_grandchildren=False,
        )
    elif pattern == "parent_child_grandchild":
        created = expand_parent_child(
            root_json=root_json,
            data_source=data_source,
            root_task=task_data,
            user=user,
            include_grandchildren=True,
        )
    elif pattern == "file_links":
        created = expand_file_links(
            root_json=root_json,
            data_source=data_source,
            root_task=task_data,
            user=user,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"未対応の処理方式です。 processing_pattern={pattern}",
        )

    return {
        "item_id": raw_item["item_id"],
        "created_task_count": created,
        "result_item_count": 1,
    }


def execute_save_json_task(*, data_source: dict, user: dict, task_data: dict) -> dict:
    payload = task_data.get("payload") or {}
    item = save_json_item(
        payload=payload.get("data"),
        data_source=data_source,
        user=user,
        batch_id=task_data["batch_id"],
        task_id=task_data["task_id"],
        item_type=normalize_text(payload.get("item_type", "item")),
        level=int(payload.get("level", 1)),
        parent_id=normalize_text(payload.get("parent_id")) or None,
        source_index=payload.get("source_index"),
        item_id=normalize_text(payload.get("fixed_item_id")) or None,
    )
    return {"item_id": item["item_id"], "result_item_count": 1}


def execute_download_file_task(*, data_source: dict, user: dict, task_data: dict) -> dict:
    payload = task_data.get("payload") or {}
    url = normalize_text(payload.get("url", ""))
    if not url:
        raise HTTPException(status_code=400, detail="取得対象ファイルURLがありません。")

    content, http_status, content_type = request_file(url, data_source)
    if http_status < 200 or http_status >= 300:
        raise HTTPException(status_code=502, detail="ファイル取得先から正常でない応答が返されました。")

    item = save_downloaded_file(
        content=content,
        content_type=content_type,
        source_url=url,
        data_source=data_source,
        user=user,
        batch_id=task_data["batch_id"],
        task_id=task_data["task_id"],
        parent_id=normalize_text(payload.get("parent_id")) or None,
        source_index=payload.get("source_index"),
        source_metadata=(
            payload.get("source_metadata")
            if isinstance(payload.get("source_metadata"), dict)
            else {}
        ),
    )
    return {"item_id": item["item_id"], "result_item_count": 1}


def execute_import(*, data_source: dict, user: dict, task_data: dict) -> dict:
    task_type = normalize_key(task_data.get("task_type", "fetch_root"))

    if task_type == "fetch_root":
        return execute_root_task(
            data_source=data_source,
            user=user,
            task_data=task_data,
        )
    if task_type == "save_json_item":
        return execute_save_json_task(
            data_source=data_source,
            user=user,
            task_data=task_data,
        )
    if task_type == "download_file":
        return execute_download_file_task(
            data_source=data_source,
            user=user,
            task_data=task_data,
        )

    raise HTTPException(
        status_code=400,
        detail=f"未対応の取込タスクです。 task_type={task_type}",
    )
