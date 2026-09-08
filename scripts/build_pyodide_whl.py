# -*- coding: utf-8 -*-
"""Build the vendored pure-python wheels Pyodide needs (reproducible).

The runtime stack is: wasm-native numpy + Pillow (loaded via pyodide.loadPackage,
they're C-ext / can't be pure-python), plus pure-python ezdxf/fpdf2/fonttools/
pyparsing/typing_extensions/defusedxml which we vendor so the browser never has to
hit PyPI.  All built with --no-deps --no-binary :all: (forced from sdist) and
--no-build-isolation (reuse installed setuptools/wheel/flit_core, so no compiler
needed — Sandbox couldn't compile cython, hence the LNK1104 if omitted).

Usage from diecut-web/:  python scripts/build_pyodide_whl.py
Outputs: static/vendor/*.whl  (py3-none-any, version-independent)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENDOR = os.path.join(ROOT, "static", "vendor")

# pure-python packages we must vendor (Pyodide doesn't ship them, and they're
# required by ezdxf/fpdf2). numpy+Pillow are excluded — they're Pyodide natives.
_PURE = [
    "ezdxf", "fpdf2",
    "fonttools",
    "pyparsing", "typing_extensions", "defusedxml",
]


def main() -> int:
    os.makedirs(VENDOR, exist_ok=True)
    # Drop any stale wheels so a rebuild is clean.
    for fn in os.listdir(VENDOR):
        if fn.endswith(".whl"):
            os.remove(os.path.join(VENDOR, fn))

    cmd = [sys.executable, "-m", "pip", "wheel", *_PURE,
           "--no-deps", "--no-binary", ":all:", "--no-build-isolation",
           "-w", VENDOR]
    print("running:", " ".join(cmd))
    rp = subprocess.run(cmd)

    if rp.returncode != 0:
        print("FAIL: wheel build exited", rp.returncode)
        print("  (try: pip install flit_core wheel setuptools first)")
        return 1

    total = 0
    print("\nbuilt wheels:")
    for fn in sorted(os.listdir(VENDOR)):
        if fn.endswith(".whl"):
            sz = os.path.getsize(os.path.join(VENDOR, fn))
            total += sz
            print(f"  {fn}  ({sz/1024:.1f} KB)")
    print(f"total vendored: {total/1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
