import xml.etree.ElementTree as ET

from fastapi import HTTPException

from app.routers.data_raw_analysis_common import (
    create_record,
)
from app.routers.data_import_common import (
    normalize_text,
)


def element_to_data(
    element: ET.Element,
) -> dict:
    children = list(element)

    data = {
        "tag":
            element.tag,
        "attributes":
            dict(element.attrib),
        "text":
            normalize_text(
                element.text
            ),
    }

    if children:
        data["children"] = [
            element_to_data(child)
            for child in children
        ]

    return data


def element_text(
    element: ET.Element,
) -> str:
    values = [
        normalize_text(value)
        for value in element.itertext()
        if normalize_text(value)
    ]

    return "\n".join(values)


def analyze_xml(
    content: bytes,
) -> tuple[list[dict], dict]:
    try:
        root = ET.fromstring(content)
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "XMLファイルを解析できません。"
                f" {type(error).__name__}: {error}"
            ),
        )

    children = list(root)

    if not children:
        children = [root]

    records = []

    for index, element in enumerate(
        children,
        start=1,
    ):
        records.append(
            create_record(
                record_type=
                    "xml_element",
                title=
                    element.tag,
                content=
                    element_text(element),
                sequence=
                    index,
                structured_data=
                    element_to_data(
                        element
                    ),
                metadata={
                    "root_tag":
                        root.tag,
                    "element_tag":
                        element.tag,
                },
            )
        )

    return records, {
        "root_tag": root.tag,
    }
