#!/usr/bin/env python3
"""Generate producer-side FastAPI artifacts from simplified specs.

This script runs two steps:
1) Rebuild `simplified/*.yaml` via `tools/build_simplified_specs.py`
2) Run openapi-generator (python-fastapi) for nncof/nupf/nnef/nsmf

Important: generation is executed with `cwd=simplified/` so relative `$ref`
resolves to simplified companion files (not original root YAMLs).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SIMPLIFIED_DIR = REPO_ROOT / "simplified"
GENERATED_ROOT = Path("/home/labry/git/ncof_generated")
GEN_JAR = Path("/home/labry/openapi-tools/openapi-generator-cli-7.13.0.jar")

TARGETS = {
    "nncof": "Nncof_EventsSubscription_PoC_ETRI_DoDo1.yaml",
    "nupf": "TS29564_Nupf_EventExposure_PoC_ETRI_DoDo1.yaml",
    "nnef": "TS29591_Nnef_EventExposure_PoC_ETRI_DoDo1.yaml",
    "nsmf": "TS29508_Nsmf_EventExposure_PoC_ETRI_DoDo1.yaml",
}


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    if not GEN_JAR.exists():
        raise SystemExit(f"openapi-generator jar not found: {GEN_JAR}")

    print("[1/2] rebuilding simplified specs...")
    _run([sys.executable, str(REPO_ROOT / "tools" / "build_simplified_specs.py")], cwd=REPO_ROOT)

    print("[2/2] generating producer artifacts...")
    for pkg, spec_file in TARGETS.items():
        out_dir = GENERATED_ROOT / pkg
        if out_dir.exists():
            shutil.rmtree(out_dir)
        cmd = [
            "java",
            "-jar",
            str(GEN_JAR),
            "generate",
            "-i",
            spec_file,
            "-g",
            "python-fastapi",
            "-o",
            str(out_dir),
            "--package-name",
            pkg,
        ]
        print(f"  - {pkg}: {spec_file} -> {out_dir}")
        _run(cmd, cwd=SIMPLIFIED_DIR)

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

