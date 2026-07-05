"""
config.py — Loads config.yaml + .env and exposes a cached AppConfig singleton.

Usage:
    from app.config import get_config
    cfg = get_config()
    print(cfg.pinecone.index_name)
"""

import os
from dataclasses import dataclass
from typing import Optional

import yaml
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Sub-config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PineconeConfig:
    index_name: str
    dimension: int


@dataclass
class CohereConfig:
    model: str


@dataclass
class AnthropicConfig:
    extraction_model: str
    generation_model: str
    temperature_extraction: float
    temperature_generation: float


@dataclass
class LoggingConfig:
    level: str
    retention_days: int


@dataclass
class EvaluationConfig:
    enabled: bool


# ---------------------------------------------------------------------------
# Root config dataclass
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    # Directory paths (string, may be relative or absolute)
    patients_root: str
    data_dir: str
    logs_dir: str
    models_dir: str

    # Sub-configs
    pinecone: PineconeConfig
    cohere: CohereConfig
    anthropic: AnthropicConfig
    logging: LoggingConfig
    evaluation: EvaluationConfig

    # API keys (from environment — never committed to git)
    cohere_api_key: str
    anthropic_api_key: str
    pinecone_api_key: str


# ---------------------------------------------------------------------------
# Internal singleton cache
# ---------------------------------------------------------------------------

_config: Optional[AppConfig] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    """Return the cached AppConfig singleton.

    On the first call the function:
    1. Calls load_dotenv() to populate os.environ from *env_path* (silently
       ignored if the file does not exist — environment variables set by the
       caller are already present in os.environ).
    2. Reads *config_path* with yaml.safe_load.
    3. Builds and caches an AppConfig instance.
    4. Raises ValueError if any required environment variable is absent.

    Subsequent calls return the cached instance without re-reading files.
    """
    global _config
    if _config is not None:
        return _config

    # Step 1 — load .env (best-effort; real key from environment wins)
    load_dotenv(dotenv_path=env_path)

    # Step 2 — read YAML
    if not os.path.isfile(config_path):
        raise FileNotFoundError(
            f"config.yaml not found at '{config_path}'. "
            "Run the application from the project root directory."
        )

    with open(config_path, "r", encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh)

    if raw is None:
        raise ValueError(f"config.yaml at '{config_path}' is empty.")

    # Step 3 — resolve required environment variables
    cohere_api_key = _require_env("COHERE_API_KEY")
    anthropic_api_key = _require_env("ANTHROPIC_API_KEY")
    pinecone_api_key = _require_env("PINECONE_API_KEY")

    # Step 4 — build typed sub-configs
    pinecone_raw = raw.get("pinecone", {})
    cohere_raw = raw.get("cohere", {})
    anthropic_raw = raw.get("anthropic", {})
    logging_raw = raw.get("logging", {})
    evaluation_raw = raw.get("evaluation", {})

    pinecone_cfg = PineconeConfig(
        index_name=_require_key(pinecone_raw, "index_name", "pinecone.index_name"),
        dimension=int(_require_key(pinecone_raw, "dimension", "pinecone.dimension")),
    )

    cohere_cfg = CohereConfig(
        model=_require_key(cohere_raw, "model", "cohere.model"),
    )

    anthropic_cfg = AnthropicConfig(
        extraction_model=_require_key(anthropic_raw, "extraction_model", "anthropic.extraction_model"),
        generation_model=_require_key(anthropic_raw, "generation_model", "anthropic.generation_model"),
        temperature_extraction=float(
            _require_key(anthropic_raw, "temperature_extraction", "anthropic.temperature_extraction")
        ),
        temperature_generation=float(
            _require_key(anthropic_raw, "temperature_generation", "anthropic.temperature_generation")
        ),
    )

    logging_cfg = LoggingConfig(
        level=logging_raw.get("level", "INFO"),
        retention_days=int(logging_raw.get("retention_days", 7)),
    )

    evaluation_cfg = EvaluationConfig(
        enabled=bool(evaluation_raw.get("enabled", False)),
    )

    # Step 5 — assemble root config
    _config = AppConfig(
        patients_root=_require_key(raw, "patients_root", "patients_root"),
        data_dir=raw.get("data_dir", "./data"),
        logs_dir=raw.get("logs_dir", "./logs"),
        models_dir=raw.get("models_dir", "./models"),
        pinecone=pinecone_cfg,
        cohere=cohere_cfg,
        anthropic=anthropic_cfg,
        logging=logging_cfg,
        evaluation=evaluation_cfg,
        cohere_api_key=cohere_api_key,
        anthropic_api_key=anthropic_api_key,
        pinecone_api_key=pinecone_api_key,
    )

    return _config


def reset_config() -> None:
    """Clear the cached singleton (useful in tests)."""
    global _config
    _config = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    """Return os.environ[name] or raise a clear ValueError."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(
            f"Required environment variable '{name}' is not set. "
            f"Add it to your .env file or set it in the environment before starting the application."
        )
    return value


def _require_key(mapping: dict, key: str, dotted_path: str):
    """Return mapping[key] or raise a clear ValueError referencing the YAML path."""
    if key not in mapping:
        raise ValueError(
            f"Required config key '{dotted_path}' is missing from config.yaml."
        )
    return mapping[key]
