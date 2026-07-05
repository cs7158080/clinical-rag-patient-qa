"""
conftest.py — pytest fixtures shared across the test suite.
"""

import os
import pytest

from app.storage.db import init_db


# ---------------------------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_dir() -> str:
    """Absolute path to the tests/synthetic_data/ directory."""
    return os.path.join(os.path.dirname(__file__), "synthetic_data")


# ---------------------------------------------------------------------------
# De-identification fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_reid_map() -> dict:
    """Return a fresh empty re-id map dict."""
    return {}


# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path) -> str:
    """Return path to a freshly initialised SQLite test database."""
    path = str(tmp_path / "clinical_rag.db")
    init_db(path)
    return path


# ---------------------------------------------------------------------------
# Config fixture (skipped if API keys are absent)
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    """Return an AppConfig built from environment variables.

    Skips the test if any required API key is missing.
    """
    required_keys = ("ANTHROPIC_API_KEY", "COHERE_API_KEY", "PINECONE_API_KEY")
    missing = [k for k in required_keys if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Missing environment variables: {', '.join(missing)}")

    from app.config import get_config, reset_config

    # Reset singleton so each test that uses this fixture gets a fresh load.
    reset_config()
    try:
        cfg = get_config()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Could not load AppConfig: {exc}")

    yield cfg

    # Clean up singleton after the test so it does not bleed into others.
    reset_config()
