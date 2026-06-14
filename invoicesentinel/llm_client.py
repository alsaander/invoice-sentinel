from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _is_local_host(host: str) -> bool:
    return host in _LOCAL_HOSTS or host.startswith("127.")


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3:8b",
        timeout: float = 120.0,
    ):
        parsed = urlparse(base_url)
        host = parsed.hostname or "localhost"

        if not _is_local_host(host):
            allow_remote = os.environ.get("ALLOW_REMOTE_LLM", "").lower() in ("true", "1", "yes")
            if not allow_remote:
                raise RuntimeError(
                    f"NFR1 HARD FAIL: refusing to send LLM requests to non-local host "
                    f"'{host}'. Set ALLOW_REMOTE_LLM=true environment variable to "
                    f"override (not recommended for compliance)."
                )

        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
    ) -> str:
        body: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        if system:
            body["system"] = system

        resp = self._client.post(
            f"{self.base_url}/api/generate",
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")

    def close(self) -> None:
        self._client.close()
