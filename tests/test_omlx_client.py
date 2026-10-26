import unittest
from unittest.mock import patch, MagicMock
import json
import io
from email.message import Message
import urllib.request
import urllib.response
from urllib.error import HTTPError
import pytest

from src.ai_engine.omlx_client import OmlxClient, OllamaClientError


@pytest.mark.parametrize("status", [307, 308])
def test_safe_opener_never_follows_redirect_or_environment_proxy(status, monkeypatch):
    requests = []
    real_builder = urllib.request.build_opener

    class SyntheticHTTP(urllib.request.HTTPHandler):
        def http_open(self, request):
            requests.append(request)
            assert request.timeout == .25
            headers = Message()
            headers["Location"] = "http://remote.invalid/evidence"
            response = urllib.response.addinfourl(io.BytesIO(b"{}"), headers, request.full_url, status)
            response.msg = "Synthetic redirect"
            return response

    monkeypatch.setenv("http_proxy", "http://proxy.invalid:9999")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr(
        urllib.request, "build_opener",
        lambda *handlers: real_builder(*handlers, SyntheticHTTP()),
    )
    client = OmlxClient(
        host="http://127.0.0.1:8000", model="synthetic",
        request_timeout_seconds=.25, safe_local_transport=True,
    )
    with pytest.raises(OllamaClientError, match=f"HTTP {status}"):
        client.list_models()
    assert len(requests) == 1
    assert requests[0].host == "127.0.0.1:8000"


def test_safe_opener_supplies_finite_timeout_to_stalled_transport(monkeypatch):
    real_builder = urllib.request.build_opener

    class SyntheticStall(urllib.request.HTTPHandler):
        def http_open(self, request):
            assert request.timeout == .125
            raise TimeoutError("synthetic stalled read")

    monkeypatch.setattr(
        urllib.request, "build_opener",
        lambda *handlers: real_builder(*handlers, SyntheticStall()),
    )
    client = OmlxClient(request_timeout_seconds=.125, safe_local_transport=True)
    with pytest.raises(TimeoutError, match="stalled read"):
        client.chat([{"role": "user", "content": "synthetic public evidence"}])


@pytest.mark.parametrize("timeout", [None, 0, -1, float("inf"), float("nan")])
def test_safe_omlx_rejects_unbounded_or_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        OmlxClient(request_timeout_seconds=timeout, safe_local_transport=True)


class TestOmlxClient(unittest.TestCase):

    def setUp(self):
        self.client = OmlxClient(
            host="http://127.0.0.1:8000",
            model="Qwen3.8-27B-4bit",
            temperature=0.7,
            max_tokens=2048
        )

    @patch('urllib.request.urlopen')
    def test_chat_non_streaming(self, mock_urlopen):
        # Mock the response for non-streaming chat
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello, world!"
                }
            }]
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = self.client.chat([
            {"role": "user", "content": "Hello"}
        ])

        self.assertEqual(result["message"]["content"], "Hello, world!")

    @patch('urllib.request.urlopen')
    def test_chat_streaming(self, mock_urlopen):
        # Mock the streaming response
        mock_response = MagicMock()
        mock_response.__iter__.return_value = [
            b'data: {"choices": [{"delta": {"role": "assistant", "content": "Hello"}}]}\n\n',
            b'data: {"choices": [{"delta": {"content": " world!"}}]}\n\n',
            b'data: [DONE]\n\n'
        ]
        mock_urlopen.return_value.__enter__.return_value = mock_response

        chunks = list(self.client.chat([
            {"role": "user", "content": "Hello"}
        ], stream=True))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["message"]["content"], "Hello")
        self.assertEqual(chunks[1]["message"]["content"], " world!")

    @patch('urllib.request.urlopen')
    def test_generate_embedding(self, mock_urlopen):
        self.client = OmlxClient(model="selected-embedding-model")
        # Mock the embedding response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": [{
                "embedding": [0.1, 0.2, 0.3]
            }]
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        embedding = self.client.generate_embedding("test text")

        self.assertEqual(embedding, [0.1, 0.2, 0.3])
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, self.client.host + "/v1/embeddings")
        self.assertEqual(json.loads(request.data), {
            "model": "selected-embedding-model",
            "input": ["test text"],
        })

    @patch('urllib.request.urlopen')
    def test_list_models(self, mock_urlopen):
        # Mock the models response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": [
                {"id": "Qwen3.8-27B-4bit"},
                {"id": "Qwen3.8-27B-8bit"}
            ]
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        models = self.client.list_models()

        self.assertEqual(models, ["Qwen3.8-27B-4bit", "Qwen3.8-27B-8bit"])

    @patch('urllib.request.urlopen')
    def test_check_connection_success(self, mock_urlopen):
        # Mock successful connection
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": []
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        self.assertTrue(self.client.check_connection())

    @patch('urllib.request.urlopen')
    def test_check_connection_failure(self, mock_urlopen):
        # Mock connection failure
        mock_urlopen.side_effect = HTTPError("http://127.0.0.1:8000", 500, "Internal Server Error", {}, None)

        self.assertFalse(self.client.check_connection())

    @patch('urllib.request.urlopen')
    def test_model_exists_success(self, mock_urlopen):
        # Mock the models response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": [
                {"id": "Qwen3.8-27B-4bit"},
                {"id": "Qwen3.8-27B-8bit"}
            ]
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        self.assertTrue(self.client.model_exists("Qwen3.8-27B-4bit"))
        self.assertTrue(self.client.model_exists("Qwen3.8-27B-8bit"))
        self.assertFalse(self.client.model_exists("Qwen3.8-27B-16bit"))

    @patch('urllib.request.urlopen')
    def test_model_exists_with_tag_suffix(self, mock_urlopen):
        # Mock the models response with tags
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": [
                {"id": "Qwen3.8-27B-4bit:latest"},
                {"id": "Qwen3.8-27B-8bit:latest"}
            ]
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response

        # Should match models with or without tag suffix
        self.assertTrue(self.client.model_exists("Qwen3.8-27B-4bit"))
        self.assertTrue(self.client.model_exists("Qwen3.8-27B-4bit:latest"))

    @patch('urllib.request.urlopen')
    def test_pull_model_raises_error(self, mock_urlopen):
        # This should raise an OllamaClientError
        with self.assertRaises(OllamaClientError):
            self.client.pull_model("test-model")

    def test_model_supports_tools(self):
        self.assertTrue(self.client.model_supports_tools("any"))

    def test_properties(self):
        self.assertEqual(self.client.host, "http://127.0.0.1:8000")
        self.assertEqual(self.client.model, "Qwen3.8-27B-4bit")
        self.assertEqual(self.client.temperature, 0.7)
        self.assertEqual(self.client.max_tokens, 2048)


if __name__ == '__main__':
    unittest.main()