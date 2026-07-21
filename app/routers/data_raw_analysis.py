from fastapi import HTTPException

from app.routers.data_import_common import (
    normalize_extension,
)
from app.routers.data_raw_csv import (
    analyze_csv,
)
from app.routers.data_raw_eml import (
    analyze_eml,
)
from app.routers.data_raw_json import (
    analyze_json,
)
from app.routers.data_raw_pdf import (
    analyze_pdf,
)
from app.routers.data_raw_txt import (
    analyze_txt,
)
from app.routers.data_raw_xlsx import (
    analyze_xlsx,
)
from app.routers.data_raw_xml import (
    analyze_xml,
)


ANALYZERS = {
    "csv":
        analyze_csv,
    "eml":
        analyze_eml,
    "json":
        analyze_json,
    "pdf":
        analyze_pdf,
    "txt":
        analyze_txt,
    "xlsx":
        analyze_xlsx,
    "xml":
        analyze_xml,
}


def analyze_file(
    extension: str,
    content: bytes,
) -> tuple[list[dict], dict]:
    normalized_extension = (
        normalize_extension(extension)
    )

    analyzer = ANALYZERS.get(
        normalized_extension
    )

    if analyzer is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f".{normalized_extension}の"
                "解析処理は実装されていません。"
            ),
        )

    return analyzer(content)
