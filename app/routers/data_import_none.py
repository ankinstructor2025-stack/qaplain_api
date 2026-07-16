import json
import re
import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.core.firebase import get_firestore_client
from app.routers.data_import_common import (
    BUCKET_NAME,
    DATA_IMPORT_COLLECTION,
    get_storage_bucket,
    normalize_text,
    validate_common_data_source,
)


def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def validate_none_data_source(
    data_source: dict,
) -> None:
    validate_common_data_source(
        data_source=data_source,
        expected_method_key="none",
    )

    if not data_source.get(
        "endpoint_url"
    ):
        raise HTTPException(
            status_code=400,
            detail="接続先URLが設定されていません。",
        )

    if (
        data_source.get(
            "http_method"
        )
        != "GET"
    ):
        raise HTTPException(
            status_code=400,
            detail="現在対応しているHTTPメソッドはGETのみです。",
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
) -> tuple[bytes, int, str]:
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
            detail="外部データの取得に失敗しました。",
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
            match.group(
                1
            )
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
        len(
            decoded_text
        )
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
    blob = get_storage_bucket().blob(
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
            detail="Cloud Storageへの保存に失敗しました。",
        )


def register_import(
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
    preview: dict,
    user: dict,
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
            user.get(
                "email",
                "",
            ),

        "updated_at":
            now,

        "updated_by":
            user.get(
                "email",
                "",
            ),
    }

    (
        get_firestore_client()
        .collection(
            DATA_IMPORT_COLLECTION
        )
        .document(
            item_id
        )
        .set(
            data
        )
    )

    return data


def execute_none_import(
    *,
    data_source: dict,
    user: dict,
) -> dict:
    validate_none_data_source(
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

    document_count = count_documents(
        content=content,
        content_type=content_type,
    )

    preview = build_preview(
        content=content,
        content_type=content_type,
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

    try:
        register_import(
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
            preview=preview,
            user=user,
        )

    except Exception as error:
        print(
            "Firestore registration error: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        raise HTTPException(
            status_code=500,
            detail="取込管理情報の登録に失敗しました。",
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

        **preview,
    }
