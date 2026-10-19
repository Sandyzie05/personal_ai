"""Offline replacement for the former live credential diagnostic."""

import io
from unittest.mock import patch

from src.ai_engine.omlx_client import OmlxClient


def test_authenticated_connection_is_offline_and_does_not_print_key(monkeypatch, capsys):
    monkeypatch.setenv("OMLX_API_KEY", "synthetic-diagnostic-key")
    client = OmlxClient(host="http://127.0.0.1:8000", model="synthetic-model")

    with patch(
        "urllib.request.urlopen",
        return_value=io.BytesIO(b'{"data": [{"id": "synthetic-model"}]}'),
    ) as transport:
        assert client.check_connection()

    request = transport.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer synthetic-diagnostic-key"
    captured = capsys.readouterr()
    assert "synthetic-diagnostic-key" not in captured.out + captured.err