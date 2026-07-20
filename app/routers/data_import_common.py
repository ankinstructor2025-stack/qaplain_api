import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import HTTPException
from google.cloud import storage

from app.core.firebase import get_firestore_client, verify_id_token

BUCKET_NAME = os.getenv("UPLOAD_BUCKET", "qaplain")
DATA_SOURCE_COLLECTION = "data_sources"
PARAMETER_COLLECTION = "parameters"
UPLOADED_FILE_COLLECTION = "uploaded_files"
DATA_IMPORT_COLLECTION = "data_import_items"
DATA_IMPORT_TASK_COLLECTION = "data_import_tasks"
TENANT_COLLECTION = "tenants"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_email(value: Any) -> str:
    return normalize_text(value).lower()


def normalize_key(value: Any) -> str:
    return normalize_text(value).lower().replace("-", "_")


def normalize_extension(value: Any) -> str:
    return normalize_text(value).lower().lstrip(".")


def authenticate_user(authorization: str) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    id_token = authorization.replace("Bearer ", "", 1).strip()
    if not id_token:
        raise HTTPException(status_code=401, detail="認証情報がありません。")

    try:
        decoded_token = verify_id_token(id_token)
    except Exception as error:
        print(f"verify_id_token error: {type(error).__name__}: {error}")
        raise HTTPException(status_code=401, detail="認証情報を確認できませんでした。")

    email = normalize_email(decoded_token.get("email", ""))
    if not email:
        raise HTTPException(status_code=401, detail="メールアドレスを取得できませんでした。")

    return {**decoded_token, "email": email}


def serialize_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}
    return value


def load_parameters(document_reference) -> list[dict]:
    documents = (
        document_reference.collection(PARAMETER_COLLECTION)
        .order_by("display_order")
        .stream()
    )
    parameters = []
    for document in documents:
        data = document.to_dict() or {}
        parameter_name = normalize_text(data.get("parameter_name", ""))
        if not parameter_name:
            continue
        parameters.append(
            {
                "parameter_id": document.id,
                "parameter_name": parameter_name,
                "parameter_value": data.get("parameter_value", ""),
                "display_order": data.get("display_order", 0),
            }
        )
    return parameters


def get_data_source(data_source_id: str) -> dict:
    normalized_id = normalize_text(data_source_id)
    if not normalized_id:
        raise HTTPException(status_code=400, detail="データソースIDが指定されていません。")

    reference = get_firestore_client().collection(DATA_SOURCE_COLLECTION).document(normalized_id)
    document = reference.get()
    if not document.exists:
        raise HTTPException(status_code=404, detail="データソースが見つかりません。")

    data = document.to_dict() or {}
    return {
        "data_source_id": document.id,
        "data_source_name": data.get("data_source_name", ""),
        "tenant_id": normalize_text(data.get("tenant_id", "")),
        "source_type": data.get("source_type", ""),
        "authentication_method_key": normalize_key(data.get("authentication_method_key", "")),
        "endpoint_url": normalize_text(data.get("endpoint_url", "")),
        "http_method": normalize_text(data.get("http_method", "GET")).upper(),
        "file_extensions": data.get("file_extensions", []),
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "client_id": data.get("client_id", ""),
        "client_secret": data.get("client_secret", ""),
        "token_url": data.get("token_url", ""),
        "scope": data.get("scope", ""),
        "parameters": load_parameters(reference),
        "enabled": data.get("enabled", True),
    }


def validate_common_data_source(data_source: dict, expected_method_key: str) -> None:
    if not data_source.get("enabled", True):
        raise HTTPException(status_code=400, detail="無効なデータソースです。")

    actual_method_key = normalize_key(data_source.get("authentication_method_key", ""))
    if actual_method_key != expected_method_key:
        raise HTTPException(
            status_code=400,
            detail=f"選択したデータソースの認証方式が{expected_method_key}ではありません。",
        )

    if not normalize_text(data_source.get("endpoint_url", "")):
        raise HTTPException(status_code=400, detail="接続先URLが設定されていません。")

    if normalize_text(data_source.get("http_method", "GET")).upper() != "GET":
        raise HTTPException(status_code=400, detail="現在対応しているHTTPメソッドはGETのみです。")


def build_requested_url(data_source: dict) -> str:
    base_url = normalize_text(data_source.get("endpoint_url", ""))
    if not base_url:
        raise HTTPException(status_code=400, detail="接続先URLが設定されていません。")

    query_values = {}
    for parameter in data_source.get("parameters", []):
        name = normalize_text(parameter.get("parameter_name", ""))
        if not name:
            continue
        value = parameter.get("parameter_value", "")
        query_values[name] = "" if value is None else str(value)

    if not query_values:
        return base_url
    delimiter = "&" if "?" in base_url else "?"
    return f"{base_url}{delimiter}{urlencode(query_values)}"


def normalize_content_type(raw_content_type: str | None) -> str:
    return str(raw_content_type or "application/octet-stream").split(";", 1)[0].strip().lower()


def get_content_extension(content_type: str, source_url: str, data_source: dict) -> str:
    content_type_map = {
        "application/json": "json",
        "application/xml": "xml",
        "text/xml": "xml",
        "text/csv": "csv",
        "text/plain": "txt",
        "text/html": "html",
        "application/pdf": "pdf",
        "application/zip": "zip",
    }
    if content_type in content_type_map:
        return content_type_map[content_type]

    source_path = urlparse(source_url).path
    if "." in source_path:
        suffix = normalize_extension(source_path.rsplit(".", 1)[-1])
        if suffix:
            return suffix

    for parameter in data_source.get("parameters", []):
        if normalize_key(parameter.get("parameter_name", "")) == "type":
            requested_type = normalize_extension(parameter.get("parameter_value", ""))
            if requested_type:
                return requested_type
    return "bin"


def build_import_preview(content: bytes, content_type: str, max_characters: int = 20000) -> dict:
    text_types = {
        "application/json", "application/xml", "text/xml",
        "text/csv", "text/plain", "text/html",
    }
    if content_type not in text_types:
        return {"preview_available": False, "preview_format": "", "preview_text": ""}

    decoded_text = content.decode("utf-8", errors="replace")
    preview_format = "text"
    if content_type == "application/json":
        preview_format = "json"
        try:
            decoded_text = json.dumps(json.loads(decoded_text), ensure_ascii=False, indent=2)
        except Exception:
            pass
    elif content_type in {"application/xml", "text/xml"}:
        preview_format = "xml"
    elif content_type == "text/html":
        preview_format = "html"
    elif content_type == "text/csv":
        preview_format = "csv"

    truncated = len(decoded_text) > max_characters
    preview_text = decoded_text[:max_characters]
    if truncated:
        preview_text += "\n\n※ 表示上限を超えたため、先頭部分のみ表示しています。"
    return {
        "preview_available": True,
        "preview_format": preview_format,
        "preview_text": preview_text,
    }


def count_json_documents(content: bytes, content_type: str) -> int | None:
    if content_type != "application/json":
        return None
    try:
        data = json.loads(content.decode("utf-8"))
    except Exception:
        return None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("items", "results", "records", "documents", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        return 1
    return None


def get_storage_bucket():
    return storage.Client().bucket(BUCKET_NAME)


def delete_from_storage(gcs_path: str) -> None:
    if not gcs_path:
        return
    try:
        blob = get_storage_bucket().blob(gcs_path)
        if blob.exists():
            blob.delete()
    except Exception as error:
        print(f"Cloud Storage delete error: {type(error).__name__}: {error}")


def save_import_file(
    *,
    content: bytes,
    content_type: str,
    data_source: dict,
    import_method: str,
    user: dict,
    source_url: str,
    http_status: int,
) -> dict:
    item_id = uuid.uuid4().hex
    extension = get_content_extension(content_type, source_url, data_source)
    gcs_path = (
        f"data-sources/{data_source['data_source_id']}/"
        f"api-imports/{item_id}/source.{extension}"
    )
    preview = build_import_preview(content, content_type)
    document_count = count_json_documents(content, content_type)

    try:
        get_storage_bucket().blob(gcs_path).upload_from_string(content, content_type=content_type)
    except Exception as error:
        print(f"Cloud Storage upload error: {type(error).__name__}: {error}")
        raise HTTPException(status_code=500, detail="Cloud Storageへの保存に失敗しました。")

    now = now_iso()
    data = {
        "item_id": item_id,
        "data_source_id": data_source["data_source_id"],
        "data_source_name": data_source.get("data_source_name", ""),
        "tenant_id": data_source.get("tenant_id", ""),
        "parent_id": None,
        "item_type": "raw_response",
        "title": data_source.get("data_source_name", ""),
        "description": "",
        "import_method": import_method,
        "requested_url": source_url,
        "http_method": data_source.get("http_method", "GET"),
        "http_status": http_status,
        "content_type": content_type,
        "extension": extension,
        "size_bytes": len(content),
        "document_count": document_count,
        "bucket_name": BUCKET_NAME,
        "gcs_path": gcs_path,
        "gcs_uri": f"gs://{BUCKET_NAME}/{gcs_path}",
        **preview,
        "status": "downloaded",
        "parameters": data_source.get("parameters", []),
        "created_at": now,
        "created_by": user.get("email", ""),
        "updated_at": now,
        "updated_by": user.get("email", ""),
    }

    try:
        get_firestore_client().collection(DATA_IMPORT_COLLECTION).document(item_id).set(data)
    except Exception as error:
        delete_from_storage(gcs_path)
        print(f"Firestore registration error: {type(error).__name__}: {error}")
        raise HTTPException(status_code=500, detail="取込管理情報の登録に失敗しました。")

    return {"message": "外部データを取り込みました。", **data}
