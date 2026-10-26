import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import win32crypt

from coatdesk_v2 import sheets
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
    assert seen["browser"]["browser"] == "coatdesk-google-login"
    assert seen["browser"]["authorization_prompt_message"] == ""
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


@pytest.mark.parametrize("config", [
    {"web": {"client_id": "test-web-client"}},
    {"installed": {"client_id": "test-client"}, "refresh_token": "fake-user-token"},
    {"installed": {"client_id": "test-client", "access_token": "fake-user-token"}},
    {"type": "service_account", "private_key": "fake-private-key"},
])
def test_authorization_rejects_web_user_tokens_and_service_account_config(tmp_path, config):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="데스크톱 앱용"):
        GoogleClient(tmp_path, True, str(path))
    assert not (tmp_path / "google-token.dpapi").exists()


@pytest.mark.parametrize("error_name", ["WSGITimeoutError", "AccessDeniedError", "ValueError"])
def test_login_errors_are_actionable_without_exposing_provider_response(tmp_path, monkeypatch, error_name):
    from google_auth_oauthlib.flow import InstalledAppFlow
    class Flow:
        def run_local_server(self, **kwargs):
            raise type(error_name, (Exception,), {})("fake-provider-private-response")
    monkeypatch.setattr(InstalledAppFlow, "from_client_config", lambda *args, **kwargs: Flow())
    path = tmp_path / "client.json"
    path.write_text(json.dumps({"installed": {"client_id": "test-client"}}), encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        GoogleClient(tmp_path, True, str(path))
    assert "fake-provider-private-response" not in str(caught.value)
    assert "Google 로그인" in str(caught.value)
    assert not (tmp_path / "google-token.dpapi").exists()


def test_default_browser_is_used_before_installed_fallback(tmp_path, monkeypatch):
    seen = []
    def open_default(*args, **kwargs):
        seen.append("default")
        return True
    monkeypatch.setattr(sheets, "_browser_candidates", lambda: [tmp_path / "msedge.exe"])
    monkeypatch.setattr(sheets, "_windows_default_browser_executable", lambda: "")
    monkeypatch.setattr(sheets.webbrowser, "get", lambda: SimpleNamespace(open=open_default))
    monkeypatch.setattr(sheets.webbrowser, "BackgroundBrowser", lambda path: SimpleNamespace(
        open=lambda *args, **kwargs: seen.append("fallback")))
    assert sheets._LoginBrowser().open("https://accounts.google.com/test")
    assert seen == ["default"]


def test_failed_default_browser_uses_installed_fallback_without_shell(tmp_path, monkeypatch):
    seen = []
    def controller(name, successful):
        def open_url(url, **kwargs):
            seen.append((name, url, kwargs))
            return successful
        return SimpleNamespace(open=open_url)
    edge = tmp_path / "msedge.exe"
    monkeypatch.setattr(sheets, "_browser_candidates", lambda: [edge])
    monkeypatch.setattr(sheets, "_windows_default_browser_executable", lambda: "")
    monkeypatch.setattr(sheets.webbrowser, "get", lambda: controller("default", False))
    monkeypatch.setattr(sheets.webbrowser, "BackgroundBrowser", lambda path: controller(path, True))
    assert sheets._LoginBrowser().open("https://accounts.google.com/test")
    assert [r[0] for r in seen] == ["default", str(edge)]
    assert seen[1][2] == {"new": 1, "autoraise": True}


def test_known_windows_default_is_directly_launched_and_other_browsers_remain_optional(tmp_path, monkeypatch):
    seen = []
    edge, chrome = tmp_path / "msedge.exe", tmp_path / "chrome.exe"
    monkeypatch.setattr(sheets, "_browser_candidates", lambda: [chrome, edge])
    monkeypatch.setattr(sheets, "_windows_default_browser_executable", lambda: "msedge.exe")
    def background(path):
        def open_url(*args, **kwargs):
            seen.append(path)
            return path == str(chrome)
        return SimpleNamespace(open=open_url)
    monkeypatch.setattr(sheets.webbrowser, "BackgroundBrowser", background)
    monkeypatch.setattr(sheets.webbrowser, "get", lambda: pytest.fail("os.startfile should not mask direct-launch failure"))
    assert sheets._LoginBrowser().open("https://accounts.google.com/test")
    assert seen == [str(edge), str(chrome)]


def test_missing_browser_gives_safe_error_without_logging_auth_url(monkeypatch):
    monkeypatch.setattr(sheets, "_browser_candidates", lambda: [])
    monkeypatch.setattr(sheets, "_windows_default_browser_executable", lambda: "")
    def missing():
        raise sheets.webbrowser.Error("not installed")
    monkeypatch.setattr(sheets.webbrowser, "get", missing)
    with pytest.raises(ValueError) as caught:
        sheets._LoginBrowser().open("https://accounts.google.com/test-private-state")
    assert "test-private-state" not in str(caught.value)
    assert "브라우저" in str(caught.value)


def test_browser_registration_cannot_recurse_when_no_system_default_exists(monkeypatch):
    monkeypatch.setattr(sheets, "_browser_candidates", lambda: [])
    monkeypatch.setattr(sheets, "_windows_default_browser_executable", lambda: "")
    monkeypatch.setattr(sheets.webbrowser, "get", lambda: sheets._LoginBrowser())
    with pytest.raises(ValueError, match="브라우저"):
        sheets._LoginBrowser().open("https://accounts.google.com/test")


def test_browser_discovery_uses_known_local_paths_not_path_command_search(tmp_path, monkeypatch):
    root = tmp_path / "Programs"
    executable = root / "Microsoft/Edge/Application/msedge.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"not an executable")
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("ProgramFiles", str(root))
    monkeypatch.setenv("PATH", str(tmp_path / "untrusted-command-directory"))
    assert sheets._browser_candidates() == [Path(executable)]
