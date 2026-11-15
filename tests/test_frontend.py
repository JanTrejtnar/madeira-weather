"""Inline skripty v docs/*.html musí být syntakticky platné: jedna chyba shodí celou stránku
(zůstane „Načítám předpověď…“), a žádný jiný test ji nezachytí."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"
PAGES = sorted(DOCS.glob("*.html"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node není k dispozici")
@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_inline_scripts_parse(page, tmp_path):
    html = page.read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/(?:ld\+)?json\")[^>]*>(.*?)</script>", html, re.DOTALL)
    for i, code in enumerate(scripts):
        js = tmp_path / f"{page.stem}-{i}.js"
        js.write_text(code, encoding="utf-8")
        res = subprocess.run(["node", "--check", str(js)], check=False, capture_output=True, text=True)
        assert res.returncode == 0, f"{page.name} skript {i}: {res.stderr[:400]}"
