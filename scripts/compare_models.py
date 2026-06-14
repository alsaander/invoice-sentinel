#!/usr/bin/env python3
"""Compare multiple Ollama models on the price-estimate reasoning step.

Usage:
    python scripts/compare_models.py
    python scripts/compare_models.py --models mistral:7b-instruct,qwen2.5:7b-instruct

The script loads config.yaml for the default model, then runs the price_estimate_v1
prompt against a fixed set of test items. Results are printed as a table.

Configuration:
    Change config.yaml model field to set the default; override with --models.
"""

import argparse
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from invoicesentinel.config import Config, load_config
from invoicesentinel.llm_client import OllamaClient
from invoicesentinel.reason import (
    build_price_estimate_prompt,
    _parse_price_estimate_json,
    _safe_float,
)
from invoicesentinel.reference_prices import load_reference_prices, find_match

TEST_ITEMS = [
    {
        "name": "Taladro drill (over-invoice test)",
        "description": "Taladro percutor inalambrico 20V profesional",
        "category": "Maquinaria",
        "quantity": 50,
        "unit_price": 8500.0,
        "currency": "USD",
    },
    {
        "name": "Disco de corte",
        "description": "Disco de corte para metal 7 pulgadas",
        "category": "Maquinaria",
        "quantity": 100,
        "unit_price": 15.0,
        "currency": "USD",
    },
    {
        "name": "Laptop genérica",
        "description": "Laptop 15.6 pulgadas i5 8GB RAM",
        "category": "Electrónica",
        "quantity": 10,
        "unit_price": 800.0,
        "currency": "USD",
    },
    {
        "name": "Cemento (bulk over-invoice)",
        "description": "Cemento Portland tipo I 42.5kg",
        "category": "Materiales de construcción",
        "quantity": 1000,
        "unit_price": 50.0,
        "currency": "USD",
    },
    {
        "name": "Camiseta (normal)",
        "description": "Camiseta manga corta 100% algodón",
        "category": "Textiles",
        "quantity": 500,
        "unit_price": 12.0,
        "currency": "USD",
    },
]


def compute_metrics(unit_price, precio_min, precio_max):
    midpoint = (precio_min + precio_max) / 2
    if midpoint == 0:
        deviation_pct = 0.0
    else:
        deviation_pct = (unit_price - midpoint) / midpoint * 100
    abs_dev = abs(deviation_pct)
    if abs_dev <= 100:
        severity = "NORMAL"
    elif abs_dev <= 200:
        severity = "MODERATE"
    else:
        severity = "HIGH"
    return midpoint, deviation_pct, severity


def run_item(client, item, ref_prices):
    """Run one item through the price estimate prompt and return results."""
    prompt = build_price_estimate_prompt(
        description=item["description"],
        category=item["category"],
        quantity=item["quantity"],
    )

    t0 = time.monotonic()
    response = client.generate(prompt)
    latency_ms = (time.monotonic() - t0) * 1000

    try:
        parsed = _parse_price_estimate_json(response)
        precio_min = _safe_float(parsed.get("precio_min"))
        precio_max = _safe_float(parsed.get("precio_max"))
        justification = parsed.get("justificacion", "")
    except (json.JSONDecodeError, ValueError):
        return {
            "success": False,
            "error": "JSON parse failed",
            "raw": response,
            "latency_ms": latency_ms,
        }

    if precio_min is None or precio_max is None:
        return {
            "success": False,
            "error": "Missing precio_min/precio_max",
            "raw": response,
            "latency_ms": latency_ms,
        }

    # Check reference CSV
    ref_match = find_match(item["description"], item["category"], ref_prices)
    midpoint, deviation_pct, severity = compute_metrics(
        item["unit_price"], precio_min, precio_max,
    )

    return {
        "success": True,
        "precio_min": precio_min,
        "precio_max": precio_max,
        "midpoint": midpoint,
        "deviation_pct": deviation_pct,
        "severity": severity,
        "justification": justification[:120] + "..." if len(justification) > 120 else justification,
        "ref_match": ref_match,
        "latency_ms": latency_ms,
    }


def main():
    parser = argparse.ArgumentParser(description="Compare models on price estimate reasoning")
    parser.add_argument(
        "--models",
        default=None,
        help="Comma-separated model names (default: from config.yaml)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    args = parser.parse_args()

    cfg: Config = load_config(args.config)
    ref_prices = load_reference_prices(cfg.reference_prices.path)

    if args.models:
        model_names = [m.strip() for m in args.models.split(",")]
    else:
        model_names = [cfg.model.name]

    rows = []
    for model_name in model_names:
        client = OllamaClient(model=model_name, base_url=cfg.model.ollama_host)
        for item in TEST_ITEMS:
            result = run_item(client, item, ref_prices)
            rows.append((model_name, item["name"], item["unit_price"], result))

    # Print results table
    header = f"{'Model':<25} {'Item':<45} {'Price':<8} {'Min':<8} {'Max':<8} {'Mid':<8} {'Dev%':<10} {'Sev':<10} {'Lat(ms)':<8}"
    sep = "-" * len(header)
    print(header)
    print(sep)

    for model_name, item_name, unit_price, result in rows:
        if result["success"]:
            print(
                f"{model_name:<25} {item_name:<45} {unit_price:<8.2f} "
                f"{result['precio_min']:<8.2f} {result['precio_max']:<8.2f} "
                f"{result['midpoint']:<8.2f} {result['deviation_pct']:<10.2f} "
                f"{result['severity']:<10} {result['latency_ms']:<8.0f}"
            )
        else:
            print(
                f"{model_name:<25} {item_name:<45} {unit_price:<8.2f} "
                f"{'FAIL':<8} {'FAIL':<8} {'FAIL':<8} {'FAIL':<10} "
                f"{result['error']:<10} {result['latency_ms']:<8.0f}"
            )
    print()


if __name__ == "__main__":
    main()
