import json
from unittest.mock import AsyncMock

from sqlalchemy.exc import IntegrityError

from scripts import production_pack


def test_cli_emits_location_but_not_sql_parameters(monkeypatch, capsys):
    secret = "credential-must-not-appear"
    error = IntegrityError("SELECT secret FROM private_config", {"key": secret}, Exception(secret))
    monkeypatch.setattr(production_pack, "_qualify", AsyncMock(side_effect=error))
    monkeypatch.setattr("sys.argv", ["production_pack.py", "qualify"])
    assert production_pack.main() == 1
    output = capsys.readouterr().out
    assert secret not in output
    assert "private_config" not in output
    result = json.loads(output)
    assert result["error"] == "IntegrityError"
    assert result["phase"] == "qualify"
    assert result["passed"] is False
    assert any(
        frame["file"] == "scripts/production_pack.py" and frame["line"] > 0
        for frame in result["trace"][0]["frames"]
    )
