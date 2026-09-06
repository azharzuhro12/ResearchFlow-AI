"""Tests for project-relative storage paths (portability requirements)."""

import os

from app.core import config


def test_hf_cache_is_project_local() -> None:
    """Embedding model downloads must land inside the project, not ~/.cache."""
    hf_home = os.environ.get("HF_HOME", "")

    assert hf_home
    assert hf_home == str(config.MODELS_DIR / "hf")
    assert config.PROJECT_ROOT in config.MODELS_DIR.parents
    assert config.MODELS_DIR.name == ".models"


def test_chroma_dir_defaults_inside_project() -> None:
    """ChromaDB storage must stay project-relative (no absolute /home paths)."""
    assert config.CHROMA_DIR == (config.PROJECT_ROOT / "data" / "chroma").resolve()
    assert "ResearchFlow-AI" in str(config.CHROMA_DIR)
