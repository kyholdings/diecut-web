# -*- coding: utf-8 -*-
"""Font subsetting: shrink NotoSansSC to only the glyphs the titles can contain.

The PDF title is deterministic and built from ``box_label + ...`` in
box_registry.generate_payload.  So the set of Chinese characters the engine can
ever put in a PDF is exactly ``{every BoxSpec.label} U {fixed title tokens}``.
We slice the 10.6MB full font down to just those glyphs -> a ~50-150KB subset
that fits the Cloudflare Pages 25MiB budget comfortably, while still embedding
every character the engine is capable of emitting.

Run from diecut-web/:  python scripts/dix_subset_font.py
Outputs:  fonts/NotoSansSC-subset.ttf   (keeps the full font for later rebuilds)
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Fixed words that appear in every title beside the box label.
# generate_payload title = f"{box_label}刀版 内 {L}x{W}x{H}mm 纸厚{T}mm"
_TITLE_TOKENS = "刀版内外纸厚xX×*0123456789.+-mm "


def _title_chars() -> set[str]:
    """Every character the engine could emit into a PDF title."""
    chars = set(_TITLE_TOKENS)
    try:
        sys.path.insert(0, ROOT)
        from box_registry import BOX_REGISTRY
        for spec in BOX_REGISTRY.values():
            chars.update(spec.label)
    except Exception:
        # box_registry not importable (e.g. no engine yet) - degrade to tokens only
        pass
    return chars


def build_subset() -> int:
    from fontTools.subset import Subsetter, Options
    from fontTools.ttLib import TTFont

    src = os.path.join(ROOT, "fonts", "NotoSansSC-Regular.ttf")
    if not os.path.exists(src):
        print("missing full font:", src)
        return 1

    chars = _title_chars()
    text = "".join(sorted(chars))
    print(f"subsetting {len(chars)} unique chars")

    font = TTFont(src)
    opts = Options()
    opts.drop_tables += ["FFTM"]
    opts.layout_features = ["*"]
    subsetter = Subsetter(options=opts)
    subsetter.populate(text=text)
    subsetter.subset(font)

    out = os.path.join(ROOT, "fonts", "NotoSansSC-subset.ttf")
    font.save(out)
    size = os.path.getsize(out)
    print(f"wrote {out}  ({size} bytes, {size/1024:.1f} KB), from {os.path.getsize(src)/1048576:.1f} MB")

    # Self-verify: every title char is present in the subset cmap.
    check = TTFont(out)
    cmap = check.getBestCmap()
    missing = [c for c in chars if ord(c) not in cmap]
    if missing:
        print("MISSING GLYPHS:", "".join(missing))
        return 1
    print("OK: subset covers all", len(chars), "title chars")
    return 0


if __name__ == "__main__":
    sys.exit(build_subset())
