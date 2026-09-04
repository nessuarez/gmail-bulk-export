"""The engine must not carry the vocabulary of the tool it was extracted from.

The split left traces behind: examples drawn from that tool's line of
business, its own word for a customer request as the canonical accent
example, a real mail domain in a fixture, one deployment's backfill window
baked into `--start`/`--end` defaults. Each was removed once; this is what
stops the next sync from quietly bringing them back.

The rule these tests encode is the one in [CLAUDE.md](../CLAUDE.md)
§ Conventions: everything in English, examples drawn from a single fictional
cast. If a banned term is genuinely needed one day, add it to `NOT_A_LEAK` or
`ALLOWED` with the reason — don't delete the test.

This file is excluded from its own scan: it has to spell the words out in order
to ban them.
"""

import re
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

# Directories holding no source of ours, or data that is gitignored anyway.
SKIPPED_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "output",
    "logs",
    "htmlcov",
}

SCANNED_SUFFIXES = {".py", ".md", ".txt", ".toml", ".json", ".yml", ".yaml", ".cfg"}

# Business vocabulary of the upstream tool, with why it is out.
FORBIDDEN = {
    "aervio": "the name of the organization the engine was extracted from",
    "peticion": "the upstream term for a customer request",
    "peticiones": "the upstream mailbox name",
    "consultor": "the upstream role name",
    "consultant": "same role in English: examples use '@Assigned/Jane Doe'",
    "agencia": "the upstream organization type",
    "hotel": "upstream line of business: use 'quarterly report' instead",
    "vuelo": "upstream line of business",
    "flight": "upstream line of business",
    "booking": "upstream line of business",
    "reserva": "upstream line of business",
    "reservas": "upstream line of business",
    "billete": "upstream line of business",
    "expediente": "the upstream case-file concept",
}

# Word-boundary match, tolerating the accented spelling of the Spanish terms.
PATTERNS = {
    term: re.compile(rf"\b{term.replace('o', '[oó]').replace('e', '[eé]')}\b", re.IGNORECASE)
    for term in FORBIDDEN
}

# Phrases where a banned word is not the business term at all. A line
# containing one of these is not a hit, wherever it appears.
NOT_A_LEAK = (
    "pre-flight",  # the auth check before a long download
    "in flight",  # concurrent requests, as in "requests in flight"
)

# Per-file exceptions: a line is allowed if it contains one of these fragments.
ALLOWED = {
    # Documenting the convention means naming what it excludes.
    "CLAUDE.md": ("examples use", "a consultant or one organization"),
}

# Non-ASCII that is in the repo on purpose: the fixtures that exercise RFC 2047
# decoding and FTS5 diacritic folding. See CLAUDE.md § Conventions.
INTENTIONAL_NON_ASCII = ("Résumé", "résumé", "Zürich")


@lru_cache(maxsize=1)
def scanned_files():
    """(relative path, lines) for every file in scope, read once for all tests."""
    files = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if path.resolve() == SELF:
            continue
        if any(part in SKIPPED_DIRS or part.endswith(".egg-info") for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append((path.relative_to(REPO_ROOT).as_posix(), content.splitlines()))
    return tuple(files)


def is_allowed(rel_path, line):
    if any(phrase in line.lower() for phrase in NOT_A_LEAK):
        return True
    return any(fragment in line for fragment in ALLOWED.get(rel_path, ()))


@pytest.mark.parametrize("term", sorted(FORBIDDEN))
def test_business_vocabulary_stays_out(term):
    pattern = PATTERNS[term]
    hits = []
    for rel_path, lines in scanned_files():
        for number, line in enumerate(lines, start=1):
            if pattern.search(line) and not is_allowed(rel_path, line):
                hits.append(f"{rel_path}:{number}: {line.strip()[:100]}")

    assert not hits, f"'{term}' is out ({FORBIDDEN[term]}):\n" + "\n".join(hits)


def test_python_sources_are_ascii_apart_from_the_deliberate_fixtures():
    """Non-ASCII in a .py file is almost always an untranslated Spanish string."""
    offenders = []
    for rel_path, lines in scanned_files():
        if not rel_path.endswith(".py"):
            continue
        for number, line in enumerate(lines, start=1):
            stripped = line
            for allowed in INTENTIONAL_NON_ASCII:
                stripped = stripped.replace(allowed, "")
            # Only letters: the arrow and bullet glyphs in console output stay.
            if any(ord(char) > 127 and char.isalpha() for char in stripped):
                offenders.append(f"{rel_path}:{number}: {line.strip()[:100]}")

    assert not offenders, "Untranslated text (or a new accented example):\n" + "\n".join(offenders)


def test_no_deployment_specific_date_defaults():
    """`--start`/`--end` must not ship one deployment's backfill window."""
    scripts = REPO_ROOT / "src" / "gmail_bulk_export" / "scripts"
    for path in sorted(scripts.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "add_argument" in line and ("--start" in line or "--end" in line):
                assert "default=" not in line, (
                    f"{path.name} pins a date default: {line.strip()}\n"
                    "Make it required, or derive it from what is on disk."
                )
