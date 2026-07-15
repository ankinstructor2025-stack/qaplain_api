import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
)
from google.cloud import storage
from pydantic import BaseModel, Field

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


router = APIRouter(
    prefix="/data-import",
    tags=["data-import"],
)


BUCKET_NAME = os.getenv(
    "UPLOAD_BUCKET",
    "qaplain",
)

DATA_SOURCE_COLLECTION = (
    "data_sources"
)

PARAMETER_COLLECTION = (
    "parameters"
)

DATA_IMPORT_ITEM_COLLECTION = (
    "data_import_items"
)


class DataImportNoneRequest(
    BaseModel
):
    data_source_id: str = Field(
        min_length=1,
    )


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def normalize_text(
    value: Any,
) -> str:
    return str(
        value or ""
    ).strip()


def normalize_email(
    value: Any,
) -> str:
    return normalize_text(
        value
    ).lower()


def normalize_source_type(
    value: Any,
) -> str:
    source_type = (
        normalize_text(
            value
        )
        .lower()
        .replace(
            "-",
            "_",
        )
    )

    aliases = {
        "public_url":
            "url",

        "public_api":
            "api",
    }

    return aliases.get(
        source_type,
        source_type,
    )


def normalize_authentication_method(
    value: Any,
) -> str:
    method = (
        normalize_text(
            value
        )
        .lower()
        .replace(
            "-",
            "_",
        )
    )

    aliases = {
        "":
            "none",

        "no_auth":
            "none",

        "no_authentication":
            "none",
    }

    return aliases.get(
        method,
        method,
    )


def authenticate_user(
    authorization: str,
) -> dict:
    if not authorization.startswith(
        "Bearer "
    ):
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid Authorization header"
            ),
        )

    id_token = authorization.replace(
        "Bearer ",
        "",
        1,
    ).strip()

    if not id_token:
        raise HTTPException(
            status_code=401,
            detail=(
                "認証情報がありません。"
            ),
        )

    try:
        decoded_token = verify_id_token(
            id_token
        )

    except Exception as error:
        print(
            "verify_id_token error: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=401,
            detail=(
                "認証情報を確認できませんでした。"
            ),
        )

    email = normalize_email(
        decoded_token.get(
            "email",
            "",
        )
    )

    if not email:
        raise HTTPException(
            status_code=401,
            detail=(
                "メールアドレスを取得できませんでした。"
            ),
        )

    return {
        **decoded_token,
        "email":
            email,
    }


def load_parameters(
    document_reference,
) -> list[dict]:
    parameter_documents = (
        document_reference
        .collection(
            PARAMETER_COLLECTION
        )
        .order_by(
            "display_order"
        )
        .stream()
    )

    parameters = []

    for document in parameter_documents:
        data = document.to_dict() or {}

        parameter_name = normalize_text(
            data.get(
                "parameter_name",
                "",
            )
        )

        if not parameter_name:
            continue

        parameters.append({
            "parameter_id":
                document.id,

            "parameter_name":
                parameter_name,

            "parameter_value":
                data.get(
                    "parameter_value",
                    "",
                ),

            "display_order":
                data.get(
                    "display_order",
                    0,
                ),
        })

    return parameters


def get_data_source(
    data_source_id: str,
) -> dict:
    normalized_id = normalize_text(
        data_source_id
    )

    if not normalized_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "データソースIDが指定されていません。"
            ),
        )

    db = get_firestore_client()

    document_reference = (
        db.collection(
            DATA_SOURCE_COLLECTION
        )
        .document(
            normalized_id
        )
    )

    document = (
        document_reference.get()
    )

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail=(
                "データソースが見つかりません。"
            ),
        )

    data = document.to_dict() or {}

    return {
        "data_source_id":
            document.id,

        "data_source_name":
            data.get(
                "data_source_name",
                "",
            ),

        "source_type":
            normalize_source_type(
                data.get(
                    "source_type",
                    "",
                )
            ),

        "endpoint_url":
            normalize_text(
                data.get(
                    "endpoint_url",
                    "",
                )
            ),

        "http_method":
            normalize_text(
                data.get(
                    "http_method",
                    "GET",
                )
            ).upper(),

        "authentication_method_key":
            normalize_authentication_method(
                data.get(
                    "authentication_method_key",
                    "",
                )
            ),

        "parameters":
            load_parameters(
                document_reference
            ),

        "enabled":
            data.get(
                "enabled",
                True,
            ),
    }


def validate_data_source(
    data_source: dict,
) -> None:
    if not data_source.get(
        "enabled",
        True,
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "無効なデータソースです。"
            ),
        )

    if data_source.get(
        "source_type"
    ) not in {
        "url",
        "api",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "URLまたはAPI型のデータソースではありません。"
            ),
        )

    if (
        data_source.get(
            "authentication_method_key"
        )
        != "none"
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "認証なしのデータソースではありません。"
            ),
        )

    if not data_source.get(
        "endpoint_url"
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "接続先URLが設定されていません。"
            ),
        )

    if (
        data_source.get(
            "http_method"
        )
        != "GET"
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "現在対応しているHTTPメソッドはGETのみです。"
            ),
        )


def build_requested_url(
    data_source: dict,
) -> str:
    base_url = data_source[
        "endpoint_url"
    ]

    query_values = {}

    for parameter in data_source.get(
        "parameters",
        []
    ):
        parameter_name = normalize_text(
            parameter.get(
                "parameter_name",
                "",
            )
        )

        if not parameter_name:
            continue

        parameter_value = parameter.get(
            "parameter_value",
            "",
        )

        if parameter_value is None:
            parameter_value = ""

        query_values[
            parameter_name
        ] = str(
            parameter_value
        )

    if not query_values:
        return base_url

    delimiter = (
        "&"
        if "?" in base_url
        else "?"
    )

    return (
        f"{base_url}"
        f"{delimiter}"
        f"{urlencode(query_values)}"
    )


def request_external_data(
    requested_url: str,
) -> tuple[
    bytes,
    int,
    str,
]:
    request = Request(
        requested_url,
        method="GET",
        headers={
            "User-Agent":
                "QAPlain-Knowledge-Studio/1.0",

            "Accept":
                "*/*",
        },
    )

    try:
        with urlopen(
            request,
            timeout=60,
        ) as response:
            content = response.read()

            status = int(
                response.status
            )

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "application/octet-stream",
                )
                .split(
                    ";",
                    1,
                )[0]
                .strip()
                .lower()
            )

            return (
                content,
                status,
                content_type,
            )

    except HTTPError as error:
        response_body = (
            error.read()
            .decode(
                "utf-8",
                errors="replace",
            )
        )

        raise HTTPException(
            status_code=502,
            detail={
                "message":
                    "接続先APIからエラーが返されました。",

                "external_status":
                    error.code,

                "external_detail":
                    response_body[:1000],
            },
        )

    except URLError as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "接続先APIへ接続できませんでした。"
                f" {error.reason}"
            ),
        )

    except Exception as error:
        print(
            "external request error: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "外部データの取得に失敗しました。"
            ),
        )


def get_extension(
    content_type: str,
    requested_url: str,
    data_source: dict,
) -> str:
    content_type_map = {
        "application/json":
            "json",

        "application/xml":
            "xml",

        "text/xml":
            "xml",

        "text/csv":
            "csv",

        "text/plain":
            "txt",

        "text/html":
            "html",

        "application/pdf":
            "pdf",

        "application/zip":
            "zip",
    }

    if content_type in content_type_map:
        return content_type_map[
            content_type
        ]

    requested_path = urlparse(
        requested_url
    ).path

    match = re.search(
        r"\.([A-Za-z0-9]+)$",
        requested_path,
    )

    if match:
        return (
            match.group(1)
            .lower()
        )

    parameter_map = {
        normalize_text(
            parameter.get(
                "parameter_name",
                "",
            )
        ).lower():
            normalize_text(
                parameter.get(
                    "parameter_value",
                    "",
                )
            ).lower()

        for parameter
        in data_source.get(
            "parameters",
            []
        )
    }

    requested_type = parameter_map.get(
        "type",
        "",
    )

    if requested_type in {
        "json",
        "xml",
        "csv",
        "txt",
        "html",
        "pdf",
        "zip",
    }:
        return requested_type

    return "bin"


def count_documents(
    content: bytes,
    content_type: str,
) -> int | None:
    if content_type != "application/json":
        return None

    try:
        data = json.loads(
            content.decode(
                "utf-8",
            )
        )

    except Exception:
        return None

    if isinstance(
        data,
        list,
    ):
        return len(
            data
        )

    if isinstance(
        data,
        dict,
    ):
        for key in (
            "items",
            "results",
            "records",
            "documents",
            "data",
        ):
            value = data.get(
                key
            )

            if isinstance(
                value,
                list,
            ):
                return len(
                    value
                )

        return 1

    return None



def build_preview(
    content: bytes,
    content_type: str,
    max_characters: int = 20000,
) -> dict:
    text_types = {
        "application/json",
        "application/xml",
        "text/xml",
        "text/csv",
        "text/plain",
        "text/html",
    }

    if content_type not in text_types:
        return {
            "preview_available":
                False,

            "preview_format":
                "",

            "preview_text":
                "",
        }

    try:
        decoded_text = content.decode(
            "utf-8",
        )

    except UnicodeDecodeError:
        decoded_text = content.decode(
            "utf-8",
            errors="replace",
        )

    preview_format = "text"

    if content_type == "application/json":
        preview_format = "json"

        try:
            parsed_data = json.loads(
                decoded_text
            )

            decoded_text = json.dumps(
                parsed_data,
                ensure_ascii=False,
                indent=2,
            )

        except Exception:
            pass

    elif content_type in {
        "application/xml",
        "text/xml",
    }:
        preview_format = "xml"

    elif content_type == "text/html":
        preview_format = "html"

    elif content_type == "text/csv":
        preview_format = "csv"

    truncated = (
        len(decoded_text)
        > max_characters
    )

    preview_text = decoded_text[
        :max_characters
    ]

    if truncated:
        preview_text += (
            "\n\n"
            "※ 表示上限を超えたため、"
            "先頭部分のみ表示しています。"
        )

    return {
        "preview_available":
            True,

        "preview_format":
            preview_format,

        "preview_text":
            preview_text,
    }

def build_gcs_path(
    data_source_id: str,
    item_id: str,
    extension: str,
) -> str:
    return (
        "data-sources/"
        f"{data_source_id}/"
        "api-imports/"
        f"{item_id}/"
        f"source.{extension}"
    )


def upload_to_storage(
    content: bytes,
    content_type: str,
    gcs_path: str,
) -> None:
    client = storage.Client()

    bucket = client.bucket(
        BUCKET_NAME
    )

    blob = bucket.blob(
        gcs_path
    )

    try:
        blob.upload_from_string(
            content,
            content_type=content_type,
        )

    except Exception as error:
        print(
            "Cloud Storage upload error: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Cloud Storageへの保存に失敗しました。"
            ),
        )


def register_import_item(
    *,
    item_id: str,
    data_source: dict,
    requested_url: str,
    http_status: int,
    content_type: str,
    extension: str,
    size_bytes: int,
    document_count: int | None,
    gcs_path: str,
    authenticated_user: dict,
) -> dict:
    now = now_iso()

    data = {
        "item_id":
            item_id,

        "data_source_id":
            data_source[
                "data_source_id"
            ],

        "data_source_name":
            data_source.get(
                "data_source_name",
                "",
            ),

        "parent_id":
            None,

        "item_type":
            "raw_response",

        "title":
            data_source.get(
                "data_source_name",
                "",
            ),

        "description":
            "",

        "requested_url":
            requested_url,

        "http_method":
            data_source.get(
                "http_method",
                "GET",
            ),

        "http_status":
            http_status,

        "content_type":
            content_type,

        "extension":
            extension,

        "size_bytes":
            size_bytes,

        "document_count":
            document_count,

        "bucket_name":
            BUCKET_NAME,

        "gcs_path":
            gcs_path,

        "gcs_uri":
            f"gs://{BUCKET_NAME}/{gcs_path}",

        "preview_available":
            preview[
                "preview_available"
            ],

        "preview_format":
            preview[
                "preview_format"
            ],

        "preview_text":
            preview[
                "preview_text"
            ],

        "status":
            "downloaded",

        "parameters":
            data_source.get(
                "parameters",
                [],
            ),

        "created_at":
            now,

        "created_by":
            authenticated_user.get(
                "email",
                "",
            ),

        "updated_at":
            now,

        "updated_by":
            authenticated_user.get(
                "email",
                "",
            ),
    }

    db = get_firestore_client()

    db.collection(
        DATA_IMPORT_ITEM_COLLECTION
    ).document(
        item_id
    ).set(
        data
    )

    return data


@router.post(
    "/none",
    status_code=201,
)
def import_none(
    request: DataImportNoneRequest,
    authorization: str = Header(...),
):
    authenticated_user = (
        authenticate_user(
            authorization
        )
    )

    data_source = get_data_source(
        request.data_source_id
    )

    validate_data_source(
        data_source
    )

    requested_url = build_requested_url(
        data_source
    )

    (
        content,
        http_status,
        content_type,
    ) = request_external_data(
        requested_url
    )

    extension = get_extension(
        content_type=content_type,
        requested_url=requested_url,
        data_source=data_source,
    )

    item_id = uuid.uuid4().hex

    gcs_path = build_gcs_path(
        data_source_id=(
            data_source[
                "data_source_id"
            ]
        ),
        item_id=item_id,
        extension=extension,
    )

    upload_to_storage(
        content=content,
        content_type=content_type,
        gcs_path=gcs_path,
    )

    document_count = count_documents(
        content=content,
        content_type=content_type,
    )

    try:
        register_import_item(
            item_id=item_id,
            data_source=data_source,
            requested_url=requested_url,
            http_status=http_status,
            content_type=content_type,
            extension=extension,
            size_bytes=len(
                content
            ),
            document_count=document_count,
            gcs_path=gcs_path,
            authenticated_user=(
                authenticated_user
            ),
        )

    except Exception as error:
        print(
            "Firestore registration error: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "取込管理情報の登録に失敗しました。"
            ),
        )

    preview = build_preview(
        content=content,
        content_type=content_type,
    )

    return {
        "message":
            "外部データを取り込みました。",

        "item_id":
            item_id,

        "file_id":
            item_id,

        "data_source_id":
            data_source[
                "data_source_id"
            ],

        "requested_url":
            requested_url,

        "http_status":
            http_status,

        "content_type":
            content_type,

        "extension":
            extension,

        "size_bytes":
            len(
                content
            ),

        "document_count":
            document_count,

        "bucket_name":
            BUCKET_NAME,

        "gcs_path":
            gcs_path,

        "gcs_uri":
            f"gs://{BUCKET_NAME}/{gcs_path}",

        "status":
            "downloaded",
    }
