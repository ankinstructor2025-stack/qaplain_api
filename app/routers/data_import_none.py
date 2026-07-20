from app.routers.data_import_common import validate_common_data_source


def build_auth_headers(data_source: dict) -> dict[str, str]:
    validate_common_data_source(data_source, "none")
    return {}
