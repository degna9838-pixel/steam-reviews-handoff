from __future__ import annotations

import os
from pathlib import Path

import pytest

from sentiment_analysis.llm_refresh import build_wsl_command, to_wsl_path

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows drive path conversion")


def test_wsl_path_conversion():
    assert to_wsl_path(Path("D:/CS_SEMA/project")) == "/mnt/d/CS_SEMA/project"


def test_wsl_command_does_not_contain_secrets():
    command = build_wsl_command(
        project_root=Path("D:/CS_SEMA/project"),
        jobs_path=Path("D:/CS_SEMA/project/jobs.jsonl"),
        predictions_path=Path("D:/CS_SEMA/project/preds.jsonl"),
        distro="Ubuntu-24.04",
        user="otojf7",
        workers=3,
        batch_size=24,
        max_batch_chars=30000,
        max_text_chars=600,
        prompt_style="simple_overall",
    )
    text = " ".join(command)
    assert "wsl.exe" in text
    assert "run_cityu_llm.sh" in text
    assert "simple_overall" in text
    assert "CITYUCS" not in text
    assert "API_KEY" not in text
