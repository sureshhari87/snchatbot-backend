import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    ".git",
    ".cache",
    ".codex_tools",
    ".verify_tools",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".pytest_cache_local",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
}
SKIP_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".sqlite",
    ".db",
    ".coverage",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
}
TOKEN_PATTERNS = [
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
]
SECRET_ASSIGNMENT = re.compile(
    r"\b(?P<key>[A-Z0-9_]*(SECRET|PASSWORD|API_KEY)[A-Z0-9_]*)\b\s*[:=]\s*[\"']?(?P<value>[^\"'\s#]+)"
)
NON_SECRET_KEY_MARKERS = {
    "EXPIRE",
    "MIN_LENGTH",
    "LOCKOUT",
    "RESET_URL",
    "VERIFY_URL",
}
SAFE_VALUE_MARKERS = {
    "",
    "0",
    "1",
    "none",
    "null",
    "false",
    "true",
}
SAFE_SUBSTRINGS = {
    "change-this",
    "replace-with",
    "example",
    "localhost",
    "settings.",
    "test",
    "ci-",
    "your-",
}


def is_text_file(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return True


def should_skip(path: Path) -> bool:
    if path.name == "secret_hygiene_check.py":
        return True
    relative_parts = set(path.relative_to(ROOT).parts)
    return bool(relative_parts & SKIP_PARTS)


def safe_assignment_value(value: str) -> bool:
    normalized = value.strip().strip('"').strip("'").lower()
    if normalized in SAFE_VALUE_MARKERS:
        return True
    return any(marker in normalized for marker in SAFE_SUBSTRINGS)


def scan_file(path: Path) -> list[str]:
    findings = []
    text = path.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern in TOKEN_PATTERNS:
            if pattern.search(line):
                findings.append(f"{path.relative_to(ROOT)}:{line_number}: token-like secret")

        assignment = SECRET_ASSIGNMENT.search(line)
        if not assignment:
            continue

        key = assignment.group("key")
        if any(marker in key for marker in NON_SECRET_KEY_MARKERS):
            continue

        if not safe_assignment_value(assignment.group("value")):
            findings.append(f"{path.relative_to(ROOT)}:{line_number}: secret-like assignment")

    return findings


def main() -> int:
    findings = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or should_skip(path) or not is_text_file(path):
            continue
        findings.extend(scan_file(path))

    if findings:
        print("Potential committed secrets found:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("Secret hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
