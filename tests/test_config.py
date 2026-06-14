import os
import tempfile

import yaml

from invoicesentinel.config import Config, load_config


def test_load_config_defaults_when_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        orig = os.getcwd()
        os.chdir(tmp)
        try:
            cfg = load_config("nonexistent.yaml")
            assert cfg.model.name == "llama3:8b"
            assert cfg.model.ollama_host == "http://localhost:11434"
            assert cfg.thresholds.normal_upper == 100
            assert cfg.thresholds.moderate_upper == 200
            assert cfg.extraction.min_text_chars == 50
            assert "Electronics" in cfg.category_vocabulary
            assert cfg.database.path == "invoicesentinel.db"
        finally:
            os.chdir(orig)


def test_load_config_from_yaml():
    data = {
        "model": {"name": "mistral:7b", "ollama_host": "http://ollama.local:11434"},
        "thresholds": {"normal_upper": 80, "moderate_upper": 150},
        "paths": {"inbox": "/custom/inbox"},
        "extraction": {"min_text_chars": 100},
        "category_vocabulary": ["Electronics", "Other"],
        "database": {"path": "/data/db.sqlite"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.yaml")
        with open(path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(path)
        assert cfg.model.name == "mistral:7b"
        assert cfg.model.ollama_host == "http://ollama.local:11434"
        assert cfg.thresholds.normal_upper == 80
        assert cfg.thresholds.moderate_upper == 150
        assert cfg.paths.inbox == "/custom/inbox"
        assert cfg.extraction.min_text_chars == 100
        assert cfg.category_vocabulary == ["Electronics", "Other"]
        assert cfg.database.path == "/data/db.sqlite"


def test_load_config_partial_overrides():
    data = {"model": {"name": "llama3:latest"}}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.yaml")
        with open(path, "w") as f:
            yaml.dump(data, f)
        cfg = load_config(path)
        assert cfg.model.name == "llama3:latest"
        assert cfg.model.ollama_host == "http://localhost:11434"
        assert cfg.thresholds.normal_upper == 100


def test_validate_thresholds_are_ints():
    cfg = Config()
    assert isinstance(cfg.thresholds.normal_upper, int)
    assert isinstance(cfg.thresholds.moderate_upper, int)


def test_category_vocabulary_contains_all_required():
    cfg = Config()
    required = [
        "Electronics", "Construction Materials", "Textiles",
        "Industrial Machinery", "Food & Beverage", "Chemicals",
        "Automotive & Spare Parts", "Other",
    ]
    for cat in required:
        assert cat in cfg.category_vocabulary, f"Missing category: {cat}"
