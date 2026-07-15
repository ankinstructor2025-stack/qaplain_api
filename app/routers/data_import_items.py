from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Query,
)

from app.core.firebase import (
    get_firestore_client,
    verify_id_token,
)


router = APIRouter(
    prefix="/data-import/items",
    tags=["data-import-items"],
)


DATA_IMPORT_ITEM_COLLECTION = (
    "data_import_items"
)


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


def serialize_value(
    value: Any,
) -> Any:
    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat()

    if hasattr(
        value,
        "isoformat",
    ):
        try:
            return value.isoformat()
        except Exception:
            pass

    if isinstance(
        value,
        list,
    ):
        return [
            serialize_value(
                item
            )
            for item in value
        ]

    if isinstance(
        value,
        dict,
    ):
        return {
            key:
                serialize_value(
                    item
                )
            for key, item
            in value.items()
        }

    return value


def document_to_dict(
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


def item_sort_value(
    item: dict,
) -> str:
    return normalize_text(
        item.get(
            "updated_at"
        )
        or item.get(
            "created_at"
        )
        or ""
    )


@router.get("")
def get_imported_items(
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

    db = get_firestore_client()

    documents = (
        db.collection(
            DATA_IMPORT_ITEM_COLLECTION
        )
        .where(
            "data_source_id",
            "==",
            normalized_data_source_id,
        )
        .stream()
    )

    items = []

    for document in documents:
        item = document_to_dict(
            document
        )

        if item.get(
            "deleted",
            False,
        ):
            continue

        items.append(
            item
        )

    items.sort(
        key=item_sort_value,
        reverse=True,
    )

    latest_item = (
        items[0]
        if items
        else None
    )

    return {
        "data_source_id":
            normalized_data_source_id,

        "latest_item":
            latest_item,

        "items":
            items,

        "count":
            len(
                items
            ),
    }
