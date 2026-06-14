import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from invoicesentinel.config import Config
from invoicesentinel.llm_client import OllamaClient
from invoicesentinel.models import (
    LineItem,
    LlmCall,
    insert_line_item,
    insert_llm_call,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt_template(version: str) -> str:
    path = PROMPTS_DIR / f"{version}.txt"
    with open(path) as f:
        return f.read()


def build_extraction_prompt(raw_text: str) -> str:
    template = load_prompt_template("extraction_v1")
    return template.replace("{raw_text}", raw_text, 1)


def build_retry_prompt() -> str:
    return load_prompt_template("json_retry_v1")


def build_extraction_retry_prompt(raw_text: str) -> str:
    template = load_prompt_template("extraction_retry_v1")
    return template.replace("{raw_text}", raw_text, 1)


def _try_strip_markdown(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_extraction_response(text: str) -> List[Dict[str, Any]]:
    """Parse extraction response, accepting multiple shapes:
    - {"items": [...]}  (canonical, after schema change)
    - [...]             (backwards compat – bare array)
    - {...}             (single object – wrap in list)
    """
    text = _try_strip_markdown(text)
    parsed = json.loads(text)

    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        if "items" in parsed:
            items = parsed["items"]
            if isinstance(items, list):
                return items
            raise ValueError(
                f"Expected 'items' key to be a JSON array, got {type(items).__name__}"
            )
        return [parsed]

    raise ValueError(f"Expected JSON object or array, got {type(parsed).__name__}")


def _validate_category(category: str, vocabulary: List[str]) -> Tuple[str, Optional[str]]:
    if category in vocabulary:
        return category, None
    return "Otro", category


def extract_line_items(
    invoice_id: int,
    raw_text: str,
    cfg: Config,
    llm_client: OllamaClient,
) -> Tuple[List[LineItem], List[LlmCall]]:
    model_name = cfg.model.name
    llm_calls: List[LlmCall] = []

    # --- First attempt ---
    prompt = build_extraction_prompt(raw_text)
    t0 = time.monotonic()
    response = llm_client.generate(prompt)
    latency_ms = int((time.monotonic() - t0) * 1000)

    call1 = LlmCall(
        invoice_id=invoice_id,
        call_type="extraction",
        prompt_version="extraction_v1",
        model=model_name,
        raw_response=response,
        latency_ms=latency_ms,
    )
    llm_calls.append(call1)

    items: List[Dict[str, Any]] = []
    try:
        items = _parse_extraction_response(response)
    except (json.JSONDecodeError, ValueError):
        logger.info("Initial JSON parse failed, retrying with extraction retry prompt")
        # --- Retry attempt ---
        retry_prompt = build_extraction_retry_prompt(raw_text)
        t0 = time.monotonic()
        response = llm_client.generate(retry_prompt)
        latency_ms = int((time.monotonic() - t0) * 1000)

        call2 = LlmCall(
            invoice_id=invoice_id,
            call_type="retry",
            prompt_version="extraction_retry_v1",
            model=model_name,
            raw_response=response,
            latency_ms=latency_ms,
        )
        llm_calls.append(call2)

        try:
            items = _parse_extraction_response(response)
        except (json.JSONDecodeError, ValueError):
            items = []

    line_items: List[LineItem] = []

    if not items:
        li = LineItem(
            invoice_id=invoice_id,
            description="(parse error)",
            severity="PARSE_ERROR",
        )
        line_items.append(li)
    else:
        for data in items:
            category, category_raw = _validate_category(
                data.get("category", ""), cfg.category_vocabulary
            )
            li = LineItem(
                invoice_id=invoice_id,
                quantity=_safe_float(data.get("quantity")),
                unit_price=_safe_float(data.get("unit_price")),
                currency=data.get("currency", ""),
                description=data.get("description", ""),
                category=category,
                category_raw=category_raw,
                severity="PENDING",
            )
            line_items.append(li)

    return line_items, llm_calls


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
