import json
import os
from unittest.mock import ANY, MagicMock, patch

import httpx
import pytest

from invoicesentinel.llm_client import OllamaClient, _is_local_host


class TestIsLocalHost:
    def test_localhost_is_local(self):
        assert _is_local_host("localhost") is True

    def test_127_0_0_1_is_local(self):
        assert _is_local_host("127.0.0.1") is True

    def test_ipv6_local_is_local(self):
        assert _is_local_host("::1") is True

    def test_zero_zero_is_local(self):
        assert _is_local_host("0.0.0.0") is True

    def test_127_prefix_is_local(self):
        assert _is_local_host("127.0.0.2") is True

    def test_remote_host_is_not_local(self):
        assert _is_local_host("remote.example.com") is False

    def test_private_ip_is_not_local_by_default(self):
        assert _is_local_host("192.168.1.1") is False


class TestNFR1Guard:
    def test_remote_host_raises_without_env(self):
        with pytest.raises(RuntimeError, match="NFR1 HARD FAIL"):
            OllamaClient(base_url="http://remote.example.com:11434")

    def test_remote_host_allowed_with_env(self, monkeypatch):
        monkeypatch.setenv("ALLOW_REMOTE_LLM", "true")
        client = OllamaClient(base_url="http://remote.example.com:11434")
        assert client.base_url == "http://remote.example.com:11434"
        client.close()

    def test_localhost_does_not_raise(self):
        client = OllamaClient(base_url="http://localhost:11434")
        assert client is not None
        client.close()

    def test_127_0_0_1_does_not_raise(self):
        client = OllamaClient(base_url="http://127.0.0.1:11434")
        assert client is not None
        client.close()


class TestGenerate:
    def test_generate_returns_response(self, mock_ollama_response_extraction):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": mock_ollama_response_extraction}

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = OllamaClient()
            result = client.generate("test prompt")

            mock_post.assert_called_once_with(
                "http://localhost:11434/api/generate",
                json={"model": client.model, "prompt": "test prompt", "stream": False, "format": "json"},
            )
            assert json.loads(result) == json.loads(mock_ollama_response_extraction)
            client.close()

    def test_generate_with_system(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "result"}

        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            client = OllamaClient()
            client.generate("prompt", system="You are a helpful assistant")

            mock_post.assert_called_once_with(
                "http://localhost:11434/api/generate",
                json={
                    "model": client.model,
                    "prompt": "prompt",
                    "system": "You are a helpful assistant",
                    "stream": False,
                    "format": "json",
                },
            )
            client.close()

    def test_generate_http_error(self):
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 400
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad request", request=MagicMock(), response=mock_resp
        )

        with patch.object(httpx.Client, "post", return_value=mock_resp):
            client = OllamaClient()
            with pytest.raises(httpx.HTTPStatusError):
                client.generate("prompt")
            client.close()
