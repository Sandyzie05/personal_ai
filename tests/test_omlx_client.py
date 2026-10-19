import unittest
from unittest.mock import patch, MagicMock
import json
from urllib.error import HTTPError

from src.ai_engine.omlx_client import OmlxClient, OllamaClientError


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
        mock_urlopen.return_value = mock_response

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
        mock_urlopen.return_value = mock_response

        chunks = list(self.client.chat([
            {"role": "user", "content": "Hello"}
        ], stream=True))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["message"]["content"], "Hello")
        self.assertEqual(chunks[1]["message"]["content"], " world!")

    @patch('urllib.request.urlopen')
    def test_generate_embedding(self, mock_urlopen):
        # Mock the embedding response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": [{
                "embedding": [0.1, 0.2, 0.3]
            }]
        }).encode('utf-8')
        mock_urlopen.return_value = mock_response

        embedding = self.client.generate_embedding("test text")

        self.assertEqual(embedding, [0.1, 0.2, 0.3])

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
        mock_urlopen.return_value = mock_response

        models = self.client.list_models()

        self.assertEqual(models, ["Qwen3.8-27B-4bit", "Qwen3.8-27B-8bit"])

    @patch('urllib.request.urlopen')
    def test_check_connection_success(self, mock_urlopen):
        # Mock successful connection
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "data": []
        }).encode('utf-8')
        mock_urlopen.return_value = mock_response

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
        mock_urlopen.return_value = mock_response

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
        mock_urlopen.return_value = mock_response

        # Should match models with or without tag suffix
        self.assertTrue(self.client.model_exists("Qwen3.8-27B-4bit"))
        self.assertTrue(self.client.model_exists("Qwen3.8-27B-4bit:latest"))

    @patch('urllib.request.urlopen')
    def test_pull_model_raises_error(self, mock_urlopen):
        # This should raise an OllamaClientError
        with self.assertRaises(OllamaClientError):
            self.client.pull_model("test-model")

    def test_model_supports_tools(self):
        # oMLX currently doesn't support tools
        self.assertFalse(self.client.model_supports_tools())

    def test_properties(self):
        self.assertEqual(self.client.host, "http://127.0.0.1:8000")
        self.assertEqual(self.client.model, "Qwen3.8-27B-4bit")
        self.assertEqual(self.client.temperature, 0.7)
        self.assertEqual(self.client.max_tokens, 2048)


if __name__ == '__main__':
    unittest.main()