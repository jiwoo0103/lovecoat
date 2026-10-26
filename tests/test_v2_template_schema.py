from google.auth.credentials import AnonymousCredentials
from googleapiclient.discovery import build

from coatdesk_v2.domain import Garment
from coatdesk_v2.sheets import build_plan, builtin_template
from coatdesk_v2.storage import Repository


def test_generated_requests_match_google_sheets_discovery_schema_without_network(tmp_path):
    repository = Repository(tmp_path)
    garment = Garment("0001", {}, fields={"category": "코트", "gender": "여성", "age": "성인", "size": "M"})
    key = repository.create("schema", "", tmp_path, 1, 1, "", [garment])
    plan = build_plan(repository.load(key), builtin_template(), [], "a" * 24, "검증")
    schemas = build("sheets", "v4", credentials=AnonymousCredentials(), static_discovery=True)._rootDesc["schemas"]

    def check(value, spec, path):
        if "$ref" in spec:
            return check(value, schemas[spec["$ref"]], path)
        if spec.get("enum"):
            assert value in spec["enum"], (path, value, spec["enum"])
        if isinstance(value, dict):
            properties = spec.get("properties", {})
            for name, item in value.items():
                assert name in properties or spec.get("additionalProperties"), (path, name)
                check(item, properties.get(name, spec.get("additionalProperties", {})), path + "." + name)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                check(item, spec.get("items", {}), path + f"[{index}]")
    check({"requests": plan.requests}, schemas["BatchUpdateSpreadsheetRequest"], "body")
