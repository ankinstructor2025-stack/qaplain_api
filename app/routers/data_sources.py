from typing import Any

from fastapi import APIRouter, HTTPException
from firebase_admin import firestore
from pydantic import BaseModel, Field

from app.core.firebase import get_firestore_client
from app.routers.data_source_common import (
    create_common_data,
    delete_connection_fields,
    normalize_file_extension,
    normalize_key,
    normalize_text,
    set_external_connection_data,
    set_processing_settings_data,
    validate_file_extensions,
    validate_processing_pattern,
    validate_processing_settings,
    validate_tenant_id,
)


router = APIRouter(
    prefix="/data-sources",
    tags=["data_sources"]
)


DATA_SOURCE_COLLECTION = "data_sources"
PARAMETER_COLLECTION = "parameters"
PARENT_DISPLAY_FIELD_COLLECTION = "parent_display_fields"


class DataSourceParameterRequest(BaseModel):
    parameter_id: str | None = None
    parameter_name: str
    parameter_value: Any = ""
    display_order: int = 0


class ParentDisplayFieldRequest(BaseModel):
    field_id: str | None = None
    label: str
    path: str
    display_order: int = 0


class DataSourceRequest(BaseModel):
    tenant_id: str
    data_source_name: str
    source_type: str
    processing_pattern: str = "raw"

    list_array_path: str | None = None
    parent_array_path: str | None = None
    child_array_path: str | None = None
    grandchild_array_path: str | None = None
    file_link_array_path: str | None = None
    file_link_field_name: str | None = None

    endpoint_url: str | None = None
    http_method: str | None = None

    file_extensions: list[str] = Field(
        default_factory=list
    )

    authentication_method_key: str | None = None

    username: str | None = None
    password: str | None = None

    client_id: str | None = None
    client_secret: str | None = None
    token_url: str | None = None
    scope: str | None = None

    enabled: bool = True

    parameters: list[DataSourceParameterRequest] = Field(
        default_factory=list
    )

    parent_display_fields: list[ParentDisplayFieldRequest] = Field(
        default_factory=list
    )


def get_db():
    return get_firestore_client()


def validate_data_source_request(
    request: DataSourceRequest,
    is_update: bool
):
    validate_tenant_id(
        request.tenant_id
    )

    data_source_name = normalize_text(
        request.data_source_name
    )

    if not data_source_name:
        raise HTTPException(
            status_code=400,
            detail="データソース名を入力してください。"
        )

    source_type = normalize_key(
        request.source_type
    )

    if source_type not in (
        "file",
        "mail",
        "url",
        "api"
    ):
        raise HTTPException(
            status_code=400,
            detail="データソース種別が正しくありません。"
        )

    method_key = normalize_key(
        request.authentication_method_key
    )

    if source_type == "file":
        if method_key != "file_upload":
            raise HTTPException(
                status_code=400,
                detail=(
                    "ファイル型のデータソースは"
                    "認証方式にfile_uploadを指定してください。"
                )
            )

        validate_file_extensions(
            request.file_extensions
        )

    if source_type in (
        "url",
        "api"
    ):
        if method_key == "file_upload":
            raise HTTPException(
                status_code=400,
                detail=(
                    "URLまたはAPI型のデータソースでは"
                    "file_uploadを使用できません。"
                )
            )

        if not normalize_text(
            request.endpoint_url
        ):
            raise HTTPException(
                status_code=400,
                detail="接続先URLを入力してください。"
            )

        if (
            validate_processing_pattern(
                request.processing_pattern
            )
            == "file_links"
        ):
            validate_file_extensions(
                request.file_extensions
            )

        validate_authentication(
            request=request,
            is_update=is_update
        )

    validate_processing_pattern(
        request.processing_pattern
    )

    validate_processing_settings(
        request
    )

    validate_parameters(
        request.parameters
    )

    validate_parent_display_fields(
        processing_pattern=request.processing_pattern,
        fields=request.parent_display_fields
    )

def validate_authentication(
    request: DataSourceRequest,
    is_update: bool
):
    method_key = normalize_key(
        request.authentication_method_key
    )

    if not method_key:
        raise HTTPException(
            status_code=400,
            detail="認証方式を選択してください。"
        )

    if method_key == "basic":
        if not normalize_text(
            request.username
        ):
            raise HTTPException(
                status_code=400,
                detail="ユーザーIDを入力してください。"
            )

        if (
            not is_update
            and not request.password
        ):
            raise HTTPException(
                status_code=400,
                detail="パスワードを入力してください。"
            )

    if is_client_credentials_method(
        method_key
    ):
        if not normalize_text(
            request.client_id
        ):
            raise HTTPException(
                status_code=400,
                detail="クライアントIDを入力してください。"
            )

        if (
            not is_update
            and not request.client_secret
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "クライアントシークレットを"
                    "入力してください。"
                )
            )


def validate_parameters(
    parameters: list[DataSourceParameterRequest]
):
    duplicate_names = set()

    for parameter in parameters:
        parameter_name = normalize_text(
            parameter.parameter_name
        )

        if not parameter_name:
            raise HTTPException(
                status_code=400,
                detail="項目名を入力してください。"
            )

        duplicate_name = parameter_name.lower()

        if duplicate_name in duplicate_names:
            raise HTTPException(
                status_code=400,
                detail="同じ項目名が重複しています。"
            )

        duplicate_names.add(
            duplicate_name
        )


def validate_parent_display_fields(
    processing_pattern: str,
    fields: list[ParentDisplayFieldRequest]
):
    normalized_pattern = validate_processing_pattern(
        processing_pattern
    )

    if (
        normalized_pattern not in (
            "parent_child",
            "parent_child_grandchild"
        )
        and fields
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "親情報表示項目は親子展開または"
                "親子孫展開で設定してください。"
            )
        )

    duplicate_paths = set()

    for field in fields:
        label = normalize_text(
            field.label
        )
        path = normalize_text(
            field.path
        )

        if not label:
            raise HTTPException(
                status_code=400,
                detail="親情報表示項目の表示名を入力してください。"
            )

        if not path:
            raise HTTPException(
                status_code=400,
                detail="親情報表示項目のJSON項目パスを入力してください。"
            )

        duplicate_path = path.lower()

        if duplicate_path in duplicate_paths:
            raise HTTPException(
                status_code=400,
                detail=(
                    "親情報表示項目に同じJSON項目パスが"
                    "重複しています。"
                )
            )

        duplicate_paths.add(
            duplicate_path
        )


def is_client_credentials_method(
    method_key: str
) -> bool:
    return method_key in (
        "credential",
        "credentials",
        "client_credentials"
    )


def create_parent_data(
    request: DataSourceRequest,
    is_update: bool
) -> dict:
    source_type = normalize_key(
        request.source_type
    )

    method_key = normalize_key(
        request.authentication_method_key
    )

    common_method_key = (
        "file_upload"
        if source_type == "file"
        else method_key
        if source_type in ("url", "api")
        else ""
    )

    data = create_common_data(
        request=request,
        method_key=common_method_key,
    )

    set_processing_settings_data(
        data=data,
        request=request,
    )

    if source_type in (
        "file",
        "mail"
    ):
        if is_update:
            delete_connection_fields(data)

            if source_type == "mail":
                data["authentication_method_key"] = (
                    firestore.DELETE_FIELD
                )

        if source_type == "file":
            data["file_extensions"] = (
                validate_file_extensions(
                    request.file_extensions
                )
            )
        else:
            data["file_extensions"] = list(
                dict.fromkeys(
                    normalize_file_extension(extension)
                    for extension in request.file_extensions
                    if normalize_file_extension(extension)
                )
            )

    if source_type in (
        "url",
        "api"
    ):
        set_external_connection_data(
            data=data,
            request=request,
        )

        data["authentication_method_key"] = (
            method_key
        )

        if (
            validate_processing_pattern(
                request.processing_pattern
            )
            == "file_links"
        ):
            data["file_extensions"] = (
                validate_file_extensions(
                    request.file_extensions
                )
            )
        else:
            data["file_extensions"] = (
                firestore.DELETE_FIELD
                if is_update
                else []
            )

        if is_update:
            data["retrieval_type"] = (
                firestore.DELETE_FIELD
            )
            data["data_format"] = (
                firestore.DELETE_FIELD
            )

        set_authentication_data(
            data=data,
            request=request,
            method_key=method_key,
            is_update=is_update
        )

    return data


def set_authentication_data(
    data: dict,
    request: DataSourceRequest,
    method_key: str,
    is_update: bool
):
    if is_update:
        clear_authentication_fields(
            data
        )

    if method_key == "basic":
        data["username"] = normalize_text(
            request.username
        )

        if request.password:
            data["password"] = (
                request.password
            )

        return

    if is_client_credentials_method(
        method_key
    ):
        data["client_id"] = normalize_text(
            request.client_id
        )

        if request.client_secret:
            data["client_secret"] = (
                request.client_secret
            )

        data["token_url"] = normalize_text(
            request.token_url
        )

        data["scope"] = normalize_text(
            request.scope
        )


def clear_authentication_fields(
    data: dict
):
    data.update({
        "username":
            firestore.DELETE_FIELD,

        "password":
            firestore.DELETE_FIELD,

        "client_id":
            firestore.DELETE_FIELD,

        "client_secret":
            firestore.DELETE_FIELD,

        "token_url":
            firestore.DELETE_FIELD,

        "scope":
            firestore.DELETE_FIELD
    })


def create_parent_display_field_data(
    field: ParentDisplayFieldRequest,
    display_order: int
) -> dict:
    return {
        "label":
            normalize_text(
                field.label
            ),

        "path":
            normalize_text(
                field.path
            ),

        "display_order":
            display_order,

        "updated_at":
            firestore.SERVER_TIMESTAMP
    }


def serialize_parent_display_field(
    document
) -> dict:
    data = document.to_dict() or {}

    return {
        "field_id":
            document.id,

        "label":
            data.get(
                "label",
                ""
            ),

        "path":
            data.get(
                "path",
                ""
            ),

        "display_order":
            data.get(
                "display_order",
                0
            )
    }


def create_parameter_data(
    parameter: DataSourceParameterRequest,
    display_order: int
) -> dict:
    return {
        "parameter_name":
            normalize_text(
                parameter.parameter_name
            ),

        "parameter_value":
            parameter.parameter_value,

        "display_order":
            display_order,

        "updated_at":
            firestore.SERVER_TIMESTAMP
    }


def serialize_data_source(
    document
) -> dict:
    data = document.to_dict() or {}

    return {
        "data_source_id":
            document.id,

        "tenant_id":
            data.get(
                "tenant_id",
                ""
            ),

        "data_source_name":
            data.get(
                "data_source_name",
                ""
            ),

        "source_type":
            data.get(
                "source_type",
                ""
            ),

        "processing_pattern":
            data.get(
                "processing_pattern",
                "raw"
            ),

        "list_array_path":
            data.get(
                "list_array_path",
                ""
            ),

        "parent_array_path":
            data.get(
                "parent_array_path",
                ""
            ),

        "child_array_path":
            data.get(
                "child_array_path",
                ""
            ),

        "grandchild_array_path":
            data.get(
                "grandchild_array_path",
                ""
            ),

        "file_link_array_path":
            data.get(
                "file_link_array_path",
                ""
            ),

        "file_link_field_name":
            data.get(
                "file_link_field_name",
                ""
            ),

        "endpoint_url":
            data.get(
                "endpoint_url",
                ""
            ),

        "http_method":
            data.get(
                "http_method",
                ""
            ),
        "file_extensions":
            data.get(
                "file_extensions",
                []
            ),

        "authentication_method_key":
            data.get(
                "authentication_method_key",
                ""
            ),

        "username":
            data.get(
                "username",
                ""
            ),

        "client_id":
            data.get(
                "client_id",
                ""
            ),

        "token_url":
            data.get(
                "token_url",
                ""
            ),

        "scope":
            data.get(
                "scope",
                ""
            ),

        "password_registered":
            bool(
                data.get(
                    "password"
                )
            ),

        "client_secret_registered":
            bool(
                data.get(
                    "client_secret"
                )
            ),

        "enabled":
            data.get(
                "enabled",
                True
            )
    }


def serialize_parameter(
    document
) -> dict:
    data = document.to_dict() or {}

    return {
        "parameter_id":
            document.id,

        "parameter_name":
            data.get(
                "parameter_name",
                ""
            ),

        "parameter_value":
            data.get(
                "parameter_value",
                ""
            ),

        "display_order":
            data.get(
                "display_order",
                0
            )
    }


def get_data_source_document(
    data_source_id: str
):
    db = get_db()

    document_reference = (
        db.collection(
            DATA_SOURCE_COLLECTION
        )
        .document(
            data_source_id
        )
    )

    document = document_reference.get()

    if not document.exists:
        raise HTTPException(
            status_code=404,
            detail="データソースが見つかりません。"
        )

    return (
        document_reference,
        document
    )


def load_parameters(
    document_reference
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

    return [
        serialize_parameter(
            document
        )
        for document in parameter_documents
    ]


def load_parent_display_fields(
    document_reference
) -> list[dict]:
    field_documents = (
        document_reference
        .collection(
            PARENT_DISPLAY_FIELD_COLLECTION
        )
        .order_by(
            "display_order"
        )
        .stream()
    )

    return [
        serialize_parent_display_field(
            document
        )
        for document in field_documents
    ]


def replace_parent_display_fields(
    document_reference,
    fields: list[ParentDisplayFieldRequest]
):
    db = get_db()
    batch = db.batch()

    existing_documents = (
        document_reference
        .collection(
            PARENT_DISPLAY_FIELD_COLLECTION
        )
        .stream()
    )

    for existing_document in existing_documents:
        batch.delete(
            existing_document.reference
        )

    for index, field in enumerate(
        fields,
        start=1
    ):
        field_id = normalize_text(
            field.field_id
        )

        if field_id:
            field_reference = (
                document_reference
                .collection(
                    PARENT_DISPLAY_FIELD_COLLECTION
                )
                .document(
                    field_id
                )
            )
        else:
            field_reference = (
                document_reference
                .collection(
                    PARENT_DISPLAY_FIELD_COLLECTION
                )
                .document()
            )

        field_data = create_parent_display_field_data(
            field=field,
            display_order=index
        )

        field_data["created_at"] = (
            firestore.SERVER_TIMESTAMP
        )

        batch.set(
            field_reference,
            field_data
        )

    batch.commit()


def replace_parameters(
    document_reference,
    parameters: list[DataSourceParameterRequest]
):
    db = get_db()
    batch = db.batch()

    existing_documents = (
        document_reference
        .collection(
            PARAMETER_COLLECTION
        )
        .stream()
    )

    for existing_document in existing_documents:
        batch.delete(
            existing_document.reference
        )

    for index, parameter in enumerate(
        parameters,
        start=1
    ):
        parameter_id = normalize_text(
            parameter.parameter_id
        )

        if parameter_id:
            parameter_reference = (
                document_reference
                .collection(
                    PARAMETER_COLLECTION
                )
                .document(
                    parameter_id
                )
            )

        else:
            parameter_reference = (
                document_reference
                .collection(
                    PARAMETER_COLLECTION
                )
                .document()
            )

        parameter_data = create_parameter_data(
            parameter=parameter,
            display_order=index
        )

        parameter_data["created_at"] = (
            firestore.SERVER_TIMESTAMP
        )

        batch.set(
            parameter_reference,
            parameter_data
        )

    batch.commit()


@router.get("")
def list_data_sources():
    db = get_db()

    documents = (
        db.collection(
            DATA_SOURCE_COLLECTION
        )
        .order_by(
            "data_source_name"
        )
        .stream()
    )

    data_sources = [
        serialize_data_source(
            document
        )
        for document in documents
    ]

    return {
        "data_sources":
            data_sources
    }


@router.get("/{data_source_id}")
def get_data_source(
    data_source_id: str
):
    (
        document_reference,
        document
    ) = get_data_source_document(
        data_source_id
    )

    data_source = serialize_data_source(
        document
    )

    data_source["parameters"] = (
        load_parameters(
            document_reference
        )
    )

    data_source["parent_display_fields"] = (
        load_parent_display_fields(
            document_reference
        )
    )

    return {
        "data_source":
            data_source
    }


@router.post("")
def create_data_source(
    request: DataSourceRequest
):
    validate_data_source_request(
        request=request,
        is_update=False
    )

    db = get_db()

    document_reference = (
        db.collection(
            DATA_SOURCE_COLLECTION
        )
        .document()
    )

    parent_data = create_parent_data(
        request=request,
        is_update=False
    )

    parent_data["created_at"] = (
        firestore.SERVER_TIMESTAMP
    )

    document_reference.set(
        parent_data
    )

    try:
        replace_parameters(
            document_reference=document_reference,
            parameters=request.parameters
        )

        replace_parent_display_fields(
            document_reference=document_reference,
            fields=request.parent_display_fields
        )

    except Exception:
        cleanup_batch = db.batch()

        for child_collection_name in (
            PARAMETER_COLLECTION,
            PARENT_DISPLAY_FIELD_COLLECTION
        ):
            for child_document in (
                document_reference
                .collection(
                    child_collection_name
                )
                .stream()
            ):
                cleanup_batch.delete(
                    child_document.reference
                )

        cleanup_batch.delete(
            document_reference
        )
        cleanup_batch.commit()
        raise

    return {
        "message":
            "データソースを登録しました。",

        "data_source_id":
            document_reference.id
    }


@router.put("/{data_source_id}")
def update_data_source(
    data_source_id: str,
    request: DataSourceRequest
):
    validate_data_source_request(
        request=request,
        is_update=True
    )

    (
        document_reference,
        existing_document
    ) = get_data_source_document(
        data_source_id
    )

    existing_data = (
        existing_document.to_dict()
        or {}
    )

    parent_data = create_parent_data(
        request=request,
        is_update=True
    )

    method_key = normalize_key(
        request.authentication_method_key
    )

    if (
        method_key == "basic"
        and not request.password
        and existing_data.get(
            "password"
        )
    ):
        parent_data["password"] = (
            existing_data["password"]
        )

    if (
        is_client_credentials_method(
            method_key
        )
        and not request.client_secret
        and existing_data.get(
            "client_secret"
        )
    ):
        parent_data["client_secret"] = (
            existing_data[
                "client_secret"
            ]
        )

    document_reference.set(
        parent_data,
        merge=True
    )

    replace_parameters(
        document_reference=document_reference,
        parameters=request.parameters
    )

    replace_parent_display_fields(
        document_reference=document_reference,
        fields=request.parent_display_fields
    )

    return {
        "message":
            "データソースを更新しました。",

        "data_source_id":
            data_source_id
    }


@router.delete("/{data_source_id}")
def delete_data_source(
    data_source_id: str
):
    document_reference, _ = (
        get_data_source_document(
            data_source_id
        )
    )

    db = get_db()
    batch = db.batch()

    for child_collection_name in (
        PARAMETER_COLLECTION,
        PARENT_DISPLAY_FIELD_COLLECTION
    ):
        child_documents = (
            document_reference
            .collection(
                child_collection_name
            )
            .stream()
        )

        for child_document in child_documents:
            batch.delete(
                child_document.reference
            )

    batch.delete(
        document_reference
    )

    batch.commit()

    return {
        "message":
            "データソースを削除しました。"
    }