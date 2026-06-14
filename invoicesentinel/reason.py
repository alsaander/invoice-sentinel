import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from invoicesentinel.config import Config
from invoicesentinel.extract import (
    _try_strip_markdown,
    load_prompt_template,
)
from invoicesentinel.llm_client import OllamaClient
from invoicesentinel.models import LineItem, LlmCall
from invoicesentinel.reference_prices import (
    MatchResult,
    build_reference_price_block,
    find_match,
    format_reference_source,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def build_price_estimate_prompt(
    description: str,
    category: str,
    quantity: Optional[float],
) -> str:
    template = load_prompt_template("price_estimate_v1")
    return (
        template
        .replace("{description}", description)
        .replace("{category}", category)
        .replace("{quantity}", str(quantity) if quantity is not None else "N/A")
    )


def build_retry_prompt() -> str:
    return load_prompt_template("json_retry_v1")


def build_price_estimate_retry_prompt(
    description: str,
    category: str,
    quantity: Optional[float],
) -> str:
    template = load_prompt_template("price_estimate_retry_v1")
    return (
        template
        .replace("{description}", description)
        .replace("{category}", category)
        .replace("{quantity}", str(quantity) if quantity is not None else "N/A")
    )


def _parse_price_estimate_json(text: str) -> Dict[str, Any]:
    text = _try_strip_markdown(text)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed


def compute_deviation_pct(unit_price: float, punto_medio: float) -> float:
    if punto_medio == 0:
        return 0.0
    return (unit_price - punto_medio) / punto_medio * 100


def classify_severity(deviation_pct: float) -> str:
    abs_dev = abs(deviation_pct)
    if abs_dev <= 100:
        return "NORMAL"
    if abs_dev <= 200:
        return "MODERATE"
    return "HIGH"


def _get_reference_midpoint(match: Dict[str, str]) -> Optional[float]:
    try:
        lo = float(match.get("price_min", 0))
        hi = float(match.get("price_max", 0))
        return (lo + hi) / 2
    except (ValueError, TypeError):
        return None


def _call_price_estimate(
    line_item: LineItem,
    description: str,
    category: str,
    quantity: Optional[float],
    cfg: Config,
    llm_client: OllamaClient,
) -> Tuple[Optional[Dict[str, Any]], Optional[LlmCall]]:
    """Make an independent price estimate call (invoiced price NOT included)."""
    prompt = build_price_estimate_prompt(description, category, quantity)

    t0 = time.monotonic()
    response = llm_client.generate(prompt)
    latency_ms = int((time.monotonic() - t0) * 1000)

    call1 = LlmCall(
        invoice_id=line_item.invoice_id,
        line_item_id=line_item.id,
        call_type="price_estimate",
        prompt_version="price_estimate_v1",
        model=cfg.model.name,
        raw_response=response,
        latency_ms=latency_ms,
    )

    try:
        parsed = _parse_price_estimate_json(response)
        return parsed, call1
    except (json.JSONDecodeError, ValueError):
        logger.info("Price estimate JSON parse failed, retrying")
        retry_prompt = build_price_estimate_retry_prompt(
            description, category, quantity,
        )
        t0 = time.monotonic()
        response = llm_client.generate(retry_prompt)
        latency_ms = int((time.monotonic() - t0) * 1000)

        call2 = LlmCall(
            invoice_id=line_item.invoice_id,
            line_item_id=line_item.id,
            call_type="retry",
            prompt_version="price_estimate_retry_v1",
            model=cfg.model.name,
            raw_response=response,
            latency_ms=latency_ms,
        )

        try:
            parsed = _parse_price_estimate_json(response)
            return parsed, call2
        except (json.JSONDecodeError, ValueError):
            return None, call2


def reason_line_item(
    line_item: LineItem,
    cfg: Config,
    llm_client: OllamaClient,
    reference_prices: List[Dict[str, str]],
) -> Tuple[LineItem, Optional[LlmCall]]:
    if line_item.currency in ("UNKNOWN", "", None):
        line_item.severity = "UNKNOWN"
        return line_item, None

    description = line_item.description or ""
    category = line_item.category or ""
    quantity = line_item.quantity
    unit_price = line_item.unit_price
    currency = line_item.currency or "UNKNOWN"

    # Step 1: Check reference CSV first (FR3.4 — overrides LLM estimate)
    ref_match_result = find_match(description, category, reference_prices)
    ref_midpoint: Optional[float] = None
    ref_source = "llm_estimate"
    ref_confidence = ""
    est_min: Optional[float] = None
    est_max: Optional[float] = None
    justification = ""
    call: Optional[LlmCall] = None

    if ref_match_result is not None:
        ref_match = ref_match_result.row
        ref_midpoint = _get_reference_midpoint(ref_match)
        ref_source = format_reference_source(ref_match)
        ref_confidence = ref_match_result.confidence
        est_min = _safe_float(ref_match.get("price_min"))
        est_max = _safe_float(ref_match.get("price_max"))

    # Step 2: If no reference match, make independent LLM price estimate
    if ref_midpoint is None:
        parsed, call = _call_price_estimate(
            line_item, description, category, quantity, cfg, llm_client,
        )

        if parsed is None:
            line_item.severity = "UNKNOWN"
            line_item.justification = call.raw_response if call else ""
            line_item.reference_source = ref_source
            return line_item, call

        est_min = _safe_float(parsed.get("precio_min"))
        est_max = _safe_float(parsed.get("precio_max"))
        justification = parsed.get("justificacion", "")

        if est_min is not None and est_max is not None:
            midpoint = (est_min + est_max) / 2
        else:
            line_item.severity = "UNKNOWN"
            line_item.justification = justification
            line_item.reference_source = "llm_estimate"
            return line_item, call
    else:
        midpoint = ref_midpoint
        justification = "(reference_csv override — no LLM estimate needed)"

    line_item.est_market_low = est_min
    line_item.est_market_high = est_max
    line_item.reference_source = ref_source
    line_item.reference_confidence = ref_confidence
    line_item.justification = justification

    if unit_price is not None and unit_price >= 0 and midpoint is not None:
        deviation_pct = compute_deviation_pct(unit_price, midpoint)
        severity = classify_severity(deviation_pct)

        # Soft caveat for broad + extreme deviation (does NOT change severity)
        if ref_confidence == "broad" and abs(deviation_pct) > 1000:
            caveat = (
                " [Nota: la comparación usa un rango de referencia genérico; "
                "se recomienda verificar si existe un precio de referencia "
                "más específico para este tipo de artículo.]"
            )
            if line_item.justification:
                line_item.justification += caveat
            else:
                line_item.justification = caveat.strip()
    else:
        deviation_pct = None
        severity = "UNKNOWN"

    line_item.deviation_pct = deviation_pct
    line_item.severity = severity

    return line_item, call


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
