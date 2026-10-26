import json
from types import SimpleNamespace

import win32crypt

from coatdesk_v2.sheets import GoogleClient, parse_template


def test_desktop_oauth_uses_pkce_browser_and_dpapi_without_network(tmp_path, monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow
    import googleapiclient.discovery
    seen = {}
    credentials = SimpleNamespace(valid=True, to_json=lambda: '{"test_value":"runtime probe"}')
    class Flow:
        def run_local_server(self, **kwargs):
            seen["browser"] = kwargs
            return credentials
    def from_config(config, scopes, **kwargs):
        seen.update(kwargs)
        return Flow()
    monkeypatch.setattr(InstalledAppFlow, "from_client_config", from_config)
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *args, **kwargs: "fake-api")
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"installed": {"client_id": "test-public-client"}}))
    client = GoogleClient(tmp_path, True, str(config))
    assert client.api == "fake-api"
    assert seen["autogenerate_code_verifier"] is True
    assert seen["browser"]["open_browser"] is True
    assert seen["browser"]["host"] == "127.0.0.1"
    encrypted = (tmp_path / "google-token.dpapi").read_bytes()
    assert b"runtime probe" not in encrypted
    assert b"runtime probe" in win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1]


def test_template_parser_retains_format_validation_and_dimensions():
    sheet = {"properties": {"sheetId": 7, "title": "template", "gridProperties": {"rowCount": 20}},
             "data": [{"startRow": 2, "startColumn": 3, "rowData": [{"values": [
                 {"userEnteredValue": {"formulaValue": "=1"}, "effectiveValue": {"numberValue": 1},
                  "userEnteredFormat": {"numberFormat": {"type": "TEXT"}}, "dataValidation": {"strict": True}}
             ]}], "rowMetadata": [{"pixelSize": 31}], "columnMetadata": [{"pixelSize": 200}]}]}
    result = parse_template(sheet)
    assert result.value(2, 3) == "=1"
    assert result.row_height == 31
    assert "effectiveValue" not in result.cells[2, 3]
    assert result.cells[2, 3]["userEnteredFormat"]
    assert result.dimensions[0]["columnMetadata"][0]["pixelSize"] == 200
