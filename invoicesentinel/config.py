from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class ModelConfig:
    name: str = "llama3:8b"
    ollama_host: str = "http://localhost:11434"


@dataclass
class ThresholdsConfig:
    normal_upper: int = 100
    moderate_upper: int = 200
    grounding_min_score: int = 50


@dataclass
class PathsConfig:
    inbox: str = "inbox"
    processed: str = "processed"
    review_high: str = "review/high"
    review_moderate: str = "review/moderate"
    review_extraction_failed: str = "review/extraction_failed"


@dataclass
class ExtractionConfig:
    min_text_chars: int = 50


@dataclass
class DatabaseConfig:
    path: str = "invoicesentinel.db"


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    category_vocabulary: List[str] = field(default_factory=lambda: [
        "Electrónica", "Materiales de construcción", "Textiles",
        "Maquinaria", "Alimentos", "Químicos",
        "Vehículos/Repuestos", "Otro",
    ])
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


def load_config(path: str = "config.yaml") -> Config:
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return Config()

    with open(path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return Config()

    cfg = Config()

    if "model" in raw:
        m = raw["model"]
        cfg.model.name = m.get("name", cfg.model.name)
        cfg.model.ollama_host = m.get("ollama_host", cfg.model.ollama_host)

    if "thresholds" in raw:
        t = raw["thresholds"]
        cfg.thresholds.normal_upper = t.get("normal_upper", cfg.thresholds.normal_upper)
        cfg.thresholds.moderate_upper = t.get("moderate_upper", cfg.thresholds.moderate_upper)
        cfg.thresholds.grounding_min_score = t.get("grounding_min_score", cfg.thresholds.grounding_min_score)

    if "paths" in raw:
        p = raw["paths"]
        cfg.paths.inbox = p.get("inbox", cfg.paths.inbox)
        cfg.paths.processed = p.get("processed", cfg.paths.processed)
        cfg.paths.review_high = p.get("review_high", cfg.paths.review_high)
        cfg.paths.review_moderate = p.get("review_moderate", cfg.paths.review_moderate)
        cfg.paths.review_extraction_failed = p.get("review_extraction_failed", cfg.paths.review_extraction_failed)

    if "extraction" in raw:
        e = raw["extraction"]
        cfg.extraction.min_text_chars = e.get("min_text_chars", cfg.extraction.min_text_chars)

    if "category_vocabulary" in raw:
        cfg.category_vocabulary = raw["category_vocabulary"]

    if "database" in raw:
        d = raw["database"]
        cfg.database.path = d.get("path", cfg.database.path)

    return cfg
