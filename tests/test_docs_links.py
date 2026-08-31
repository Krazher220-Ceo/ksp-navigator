"""tests/test_docs_links.py — ссылки в документации ведут туда, где что-то есть.

Находка 12 AUDIT.md: `README.md` ссылался на `PLAN_STAGE1.md` — файл,
решением автора от 27.08.2026 убранный в `archive/` и исключённый из
репозитория. Ссылка была битой и вела читателя ровно в ту папку, которую
`CLAUDE.md` запрещает открывать. README — первый файл, который откроет
новый человек, и проверять его глазами каждый раз никто не будет.

Что осознанно не делает: не ходит по внешним ссылкам (это сеть, а
обычный прогон обязан работать без неё) и не проверяет якоря внутри
файлов — только существование файла, на который указывает относительная
ссылка.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHECKED_DOCS = ("README.md", "launchd/README.md")

# [текст](цель) — цель без ведущего http/https и без якоря-«решётки».
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _relative_links(markdown_path: Path) -> list[str]:
    text = markdown_path.read_text(encoding="utf-8")
    links = []
    for target in _LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        links.append(target.split("#", 1)[0])
    return [link for link in links if link]


@pytest.mark.parametrize("doc", CHECKED_DOCS)
def test_relative_links_point_to_existing_files(doc):
    doc_path = PROJECT_ROOT / doc
    if not doc_path.exists():
        pytest.skip(f"{doc} в проекте нет")
    broken = [
        link for link in _relative_links(doc_path) if not (doc_path.parent / link).exists()
    ]
    assert not broken, f"{doc}: битые ссылки — {broken}"


def test_link_scanner_actually_finds_links():
    """Страховка от сломавшегося сканера: тест выше был бы зелёным и на
    нулевом списке ссылок."""
    assert len(_relative_links(PROJECT_ROOT / "README.md")) >= 3
