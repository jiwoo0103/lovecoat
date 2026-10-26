import json

import pytest

from scripts.build_v2 import desktop_config


def test_only_desktop_configuration_can_be_packaged(tmp_path):
    path = tmp_path / "client.json"
    config = {"installed": {"client_id": "public-test-client", "client_secret": "desktop-test-setting",
              "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
              "redirect_uris": ["http://localhost"]}}
    path.write_text(json.dumps(config), encoding="utf-8")
    assert desktop_config(path) == config
    config["installed"]["refresh_token"] = "must-not-ship"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError) as error:
        desktop_config(path)
    assert "must-not-ship" not in str(error.value)
    path.write_text(json.dumps({"type": "service_account", "private_key": "must-not-ship"}), encoding="utf-8")
    with pytest.raises(ValueError):
        desktop_config(path)
