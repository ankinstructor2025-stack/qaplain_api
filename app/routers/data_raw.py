from fastapi import (
    APIRouter,
    Header,
    Query,
)
from pydantic import BaseModel, Field

from app.core.firebase import (
    get_firestore_client,
)
from app.routers.data_import_common import (
    authenticate_user,
    serialize_value,
)
from app.routers.data_raw_processor import (
    RAW_DOCUMENT_COLLECTION,
    RAW_RECORD_SUBCOLLECTION,
    process_source_file,
)
from app.routers.data_raw_tasks import (
    create_batch,
    execute_data_raw_worker,
    get_analysis_summary,
)


router = APIRouter(
    prefix="/data-raw",
    tags=["data-raw"],
)


class DataRawProcessRequest(
    BaseModel
):
    source_type: str = Field(
        min_length=1,
    )

    source_id: str = Field(
        min_length=1,
    )

    overwrite: bool = True


class DataRawBatchRequest(
    BaseModel
):
    data_source_id: str = Field(
        min_length=1,
    )


class DataRawWorkerRequest(
    BaseModel
):
    task_type: str = Field(
        min_length=1,
    )

    batch_id: str = Field(
        min_length=1,
    )

    source_type: str = ""

    source_id: str = ""


@router.post(
    "/process",
    status_code=201,
)
def process_data_raw(
    request: DataRawProcessRequest,
    authorization: str = Header(...),
):
    user = authenticate_user(
        authorization
    )

    return process_source_file(
        source_type=
            request.source_type,
        source_id=
            request.source_id,
        overwrite=
            request.overwrite,
        user=
            user,
    )


@router.get(
    "/summary",
)
def get_data_raw_summary(
    data_source_id: str = Query(
        ...,
        min_length=1,
    ),
    authorization: str = Header(...),
):
    authenticate_user(
        authorization
    )

    return serialize_value(
        get_analysis_summary(
            data_source_id
        )
    )


@router.post(
    "/batch",
    status_code=202,
)
def start_data_raw_batch(
    request: DataRawBatchRequest,
    authorization: str = Header(...),
):
    user = authenticate_user(
        authorization
    )

    return create_batch(
        data_source_id=
            request.data_source_id,
        user=
            user,
    )


@router.post(
    "/tasks/worker",
    include_in_schema=False,
)
def execute_worker(
    request: DataRawWorkerRequest,
    authorization: str = Header(...),
):
    return execute_data_raw_worker(
        request=
            request,
        authorization=
            authorization,
    )


@router.get(
    "/documents/{document_id}",
)
def get_data_raw_document(
    document_id: str,
    authorization: str = Header(...),
):
    authenticate_user(
        authorization
    )

    document = (
        get_firestore_client()
        .collection(
            RAW_DOCUMENT_COLLECTION
        )
        .document(document_id)
        .get()
    )

    if not document.exists:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail=(
                "元データが"
                "見つかりません。"
            ),
        )

    data = (
        document.to_dict()
        or {}
    )

    data["document_id"] = (
        document.id
    )

    return serialize_value(data)


@router.get(
    "/documents/{document_id}/records",
)
def get_data_raw_records(
    document_id: str,
    limit: int = Query(
        100,
        ge=1,
        le=500,
    ),
    authorization: str = Header(...),
):
    authenticate_user(
        authorization
    )

    document_reference = (
        get_firestore_client()
        .collection(
            RAW_DOCUMENT_COLLECTION
        )
        .document(document_id)
    )

    documents = (
        document_reference
        .collection(
            RAW_RECORD_SUBCOLLECTION
        )
        .order_by("sequence")
        .limit(limit)
        .stream()
    )

    records = []

    for document in documents:
        data = (
            document.to_dict()
            or {}
        )

        data["record_id"] = (
            document.id
        )

        records.append(
            serialize_value(data)
        )

    return {
        "document_id":
            document_id,
        "records":
            records,
        "count":
            len(records),
    }
