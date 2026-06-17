"""Evaluation recomputation from a complete pipeline run."""

from __future__ import annotations

import json
from pathlib import Path

from fcapsule.io.case_loader import load_case
from fcapsule.pipeline import investigate_case


def recompute_evaluation(case_dir: str | Path, output_dir: str | Path) -> dict:
    investigate_case(case_dir, output_dir)
    return json.loads((Path(output_dir) / "evaluation.json").read_text(encoding="utf-8"))
