from email import policy
from email.parser import BytesParser

from fastapi import HTTPException

from app.routers.data_raw_analysis_common import (
    create_record,
    split_long_text,
)
from app.routers.data_import_common import (
    normalize_text,
)


def extract_email_body(
    message,
) -> tuple[str, str]:
    plain_parts = []
    html_parts = []

    if message.is_multipart():
        for part in message.walk():
            if (
                part.get_content_disposition()
                == "attachment"
            ):
                continue

            content_type = (
                part.get_content_type()
            )

            try:
                value = part.get_content()
            except Exception:
                payload = (
                    part.get_payload(
                        decode=True
                    )
                    or b""
                )

                charset = (
                    part.get_content_charset()
                    or "utf-8"
                )

                value = payload.decode(
                    charset,
                    errors="replace",
                )

            if content_type == "text/plain":
                plain_parts.append(
                    str(value)
                )

            elif content_type == "text/html":
                html_parts.append(
                    str(value)
                )

    else:
        try:
            value = message.get_content()
        except Exception:
            value = ""

        if (
            message.get_content_type()
            == "text/html"
        ):
            html_parts.append(
                str(value)
            )
        else:
            plain_parts.append(
                str(value)
            )

    return (
        "\n".join(plain_parts).strip(),
        "\n".join(html_parts).strip(),
    )


def analyze_eml(
    content: bytes,
) -> tuple[list[dict], dict]:
    try:
        message = BytesParser(
            policy=policy.default
        ).parsebytes(content)
    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "EMLファイルを解析できません。"
                f" {type(error).__name__}: {error}"
            ),
        )

    plain_body, html_body = (
        extract_email_body(message)
    )

    body = plain_body or html_body

    headers = {
        "subject":
            normalize_text(
                message.get("subject")
            ),
        "from":
            normalize_text(
                message.get("from")
            ),
        "to":
            normalize_text(
                message.get("to")
            ),
        "cc":
            normalize_text(
                message.get("cc")
            ),
        "date":
            normalize_text(
                message.get("date")
            ),
        "message_id":
            normalize_text(
                message.get("message-id")
            ),
    }

    attachments = []

    for part in message.iter_attachments():
        payload = (
            part.get_payload(
                decode=True
            )
            or b""
        )

        attachments.append({
            "file_name":
                part.get_filename() or "",
            "content_type":
                part.get_content_type(),
            "size_bytes":
                len(payload),
        })

    chunks = split_long_text(body)
    records = []

    for index, chunk in enumerate(
        chunks,
        start=1,
    ):
        title = headers["subject"]

        if len(chunks) > 1:
            title = (
                f"{title} "
                f"({index}/{len(chunks)})"
            )

        records.append(
            create_record(
                record_type=
                    "email_body",
                title=
                    title,
                content=
                    chunk,
                sequence=
                    index,
                metadata={
                    **headers,
                    "body_format": (
                        "plain"
                        if plain_body
                        else "html"
                    ),
                },
            )
        )

    return records, {
        **headers,
        "attachments":
            attachments,
    }
