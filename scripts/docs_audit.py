#!/usr/bin/env python3
"""Audit and regenerate Sentinel's agent-facing documentation indexes.

Default mode is read-only and exits non-zero for hard drift (bad index tags,
missing index entries, broken relative links, undocumented routes). Line-count
drift is reported as a warning because many legacy module docs still need
gradual cleanup.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INDEX = DOCS / "INDEX.txt"
ROUTE_APPENDIX = DOCS / "backend" / "api-routes-appendix.md"

TAGS = {
    "arch",
    "backend",
    "inference",
    "frontend",
    "router",
    "deployment",
    "decision",
    "operations",
    "testing",
    "benchmark",
    "scripts",
    "conventions",
    "fmv",
    "imagery",
    "sam3",
    "ontology",
    "auth",
    "gpu",
}

SECTION_TAGS = {
    "architecture": "arch",
    "backend": "backend",
    "backend-routers": "router",
    "inference": "inference",
    "frontend": "frontend",
    "deployment": "deployment",
    "operations": "operations",
    "decisions": "decision",
    "testing": "testing",
    "benchmarks": "benchmark",
    "scripts": "scripts",
    "conventions": "conventions",
    "archive": "decision",
}

LINK_RE = re.compile(r"\]\(([^)]+)\)")
PATH_RE = re.compile(r"^\*\*Path:\*\*\s*(.+)$", re.MULTILINE)
LINES_RE = re.compile(r"^\*\*Lines:\*\*\s*~?(\d+)", re.MULTILINE)
MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCED_RE = re.compile(r"```.*?```", re.DOTALL)


@dataclass(frozen=True)
class IndexRecord:
    path: str
    tags: str
    summary: str


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    source: str


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def docs_rel(path: Path) -> str:
    return path.relative_to(DOCS).as_posix()


def iter_doc_files() -> list[Path]:
    return sorted(
        p for p in DOCS.rglob("*.md")
        if p.name != "README.md" and "superpowers" not in p.parts
    )


def read_index() -> dict[str, IndexRecord]:
    records: dict[str, IndexRecord] = {}
    if not INDEX.exists():
        return records
    lines = INDEX.read_text(encoding="utf-8").splitlines()
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            raise ValueError(f"{INDEX}:{lineno}: expected path|tags|summary")
        records[parts[0]] = IndexRecord(*parts)
    return records


def infer_tags(path: str, previous: str = "") -> str:
    valid = [tag for tag in previous.split(",") if tag in TAGS]
    if valid:
        return ",".join(dict.fromkeys(valid))
    first = path.split("/", 1)[0]
    tag = SECTION_TAGS.get(first, "conventions")
    extras: list[str] = []
    text = path.lower()
    for needle, extra in (
        ("fmv", "fmv"),
        ("imagery", "imagery"),
        ("sam3", "sam3"),
        ("ontology", "ontology"),
        ("auth", "auth"),
        ("gpu", "gpu"),
    ):
        if needle in text and extra != tag:
            extras.append(extra)
    return ",".join(dict.fromkeys([tag, *extras]))


def infer_summary(path: Path, previous: str = "") -> str:
    if previous:
        return previous
    text = path.read_text(encoding="utf-8", errors="ignore")
    purpose = re.search(r"^## Purpose\s*\n+(.+?)(?:\n\n|$)", text, re.MULTILINE | re.DOTALL)
    if purpose:
        summary = " ".join(purpose.group(1).split())
    else:
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("# ")), path.stem)
        summary = title
    return summary[:140]


def write_index() -> None:
    previous = read_index()
    records: list[IndexRecord] = []
    for path in iter_doc_files():
        rpath = docs_rel(path)
        old = previous.get(rpath)
        records.append(IndexRecord(
            rpath,
            infer_tags(rpath, old.tags if old else ""),
            infer_summary(path, old.summary if old else ""),
        ))
    body = ["path|tags|summary"]
    body.extend(f"{r.path}|{r.tags}|{r.summary}" for r in sorted(records, key=lambda r: r.path))
    INDEX.write_text("\n".join(body) + "\n", encoding="utf-8")


def audit_index() -> list[str]:
    errors: list[str] = []
    records = read_index()
    paths = list(records)
    if paths != sorted(paths):
        errors.append("docs/INDEX.txt is not sorted by path")
    expected = {docs_rel(p) for p in iter_doc_files()}
    actual = set(records)
    for missing in sorted(expected - actual):
        errors.append(f"INDEX missing {missing}")
    for stale in sorted(actual - expected):
        errors.append(f"INDEX stale entry {stale}")
    for record in records.values():
        bad = [tag for tag in record.tags.split(",") if tag not in TAGS]
        if bad:
            errors.append(f"INDEX bad tag(s) for {record.path}: {','.join(bad)}")
    return errors


def audit_links() -> list[str]:
    errors: list[str] = []
    for doc in iter_doc_files() + [DOCS / "README.md"]:
        text = FENCED_RE.sub("", doc.read_text(encoding="utf-8", errors="ignore"))
        for raw in LINK_RE.findall(text):
            target = raw.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "app://")):
                continue
            if target.startswith("file://"):
                errors.append(f"{rel(doc)}: file:// link is not portable: {raw}")
                continue
            target_path = (doc.parent / target).resolve()
            if not target_path.exists():
                errors.append(f"{rel(doc)}: broken link {raw}")
    return errors


def audit_lines() -> list[str]:
    warnings: list[str] = []
    for doc in iter_doc_files():
        if docs_rel(doc).startswith("decisions/"):
            continue
        text = doc.read_text(encoding="utf-8", errors="ignore")
        line_match = LINES_RE.search(text)
        path_match = PATH_RE.search(text)
        if not line_match or not path_match:
            continue
        declared = int(line_match.group(1))
        targets = MD_LINK_RE.findall(path_match.group(1))
        if len(targets) != 1:
            continue
        target = targets[0].split("#", 1)[0]
        if target.endswith(".md"):
            continue
        source = (doc.parent / target).resolve()
        if not source.exists() or not source.is_file():
            continue
        actual = len(source.read_text(encoding="utf-8", errors="ignore").splitlines())
        if declared and abs(actual - declared) / max(actual, 1) > 0.10:
            warnings.append(f"{rel(doc)}: Lines ~{declared}, actual {actual} for {rel(source)}")
    return warnings


def _const_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _join(prefix: str, path: str) -> str:
    if not path:
        return prefix or "/"
    if not path.startswith("/"):
        path = "/" + path
    if prefix and prefix != "/":
        return (prefix.rstrip("/") + path).replace("//", "/")
    return path


def extract_routes() -> list[Route]:
    routes: list[Route] = []
    methods = {"get", "post", "put", "patch", "delete", "options", "head", "websocket"}
    for source in sorted((ROOT / "backend").rglob("*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        prefixes: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id == "APIRouter":
                    prefix = ""
                    for kw in node.value.keywords:
                        if kw.arg == "prefix":
                            prefix = _const_str(kw.value) or ""
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            prefixes[target.id] = prefix
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                method = dec.func.attr.lower()
                if method not in methods:
                    continue
                owner = dec.func.value
                if not isinstance(owner, ast.Name):
                    continue
                path = _const_str(dec.args[0]) if dec.args else ""
                if path is None:
                    continue
                full = _join(prefixes.get(owner.id, ""), path)
                routes.append(Route("WS" if method == "websocket" else method.upper(), full, rel(source)))
    return sorted(set(routes), key=lambda r: (r.path, r.method, r.source))


def write_route_appendix() -> None:
    routes = extract_routes()
    lines = [
        "# API Routes Appendix",
        "",
        "**Path:** [docs/backend/api-routes-appendix.md](api-routes-appendix.md)",
        "__LINES_PLACEHOLDER__",
        "**Depends on:** backend FastAPI decorators",
        "",
        "## Purpose",
        "",
        "Generated compact appendix of FastAPI and WebSocket decorators found under `backend/`.",
        "",
        "## Why this Design",
        "",
        "[api-routes-reference.md](api-routes-reference.md) groups routes for humans; this appendix gives agents an exact path list for drift checks.",
        "",
        "## Key Symbols",
        "",
        "| Method | Path | Source |",
        "|---|---|---|",
    ]
    for route in routes:
        lines.append(f"| `{route.method}` | `{route.path}` | [{route.source}](../../{route.source}) |")
    lines.extend([
        "",
        "## Inputs / Outputs",
        "",
        "Input: route decorators in backend Python files. Output: this route table.",
        "",
        "## Failure Modes",
        "",
        "Dynamic routes whose path is not a string literal are skipped and should be documented manually.",
        "",
        "## Cross-References",
        "",
        "- [api-routes-reference.md](api-routes-reference.md)",
        "- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)",
    ])
    lines[3] = f"**Lines:** ~{len(lines)}"
    ROUTE_APPENDIX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit_routes() -> list[str]:
    docs_text = ""
    for path in (DOCS / "backend").glob("api-routes*.md"):
        docs_text += path.read_text(encoding="utf-8", errors="ignore") + "\n"
    missing = []
    for route in extract_routes():
        if route.path not in docs_text:
            missing.append(f"Route missing from API docs: {route.method} {route.path} ({route.source})")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-index", action="store_true", help="regenerate docs/INDEX.txt")
    parser.add_argument("--write-route-appendix", action="store_true", help="regenerate docs/backend/api-routes-appendix.md")
    parser.add_argument("--print-routes", action="store_true", help="print extracted routes")
    args = parser.parse_args()

    if args.write_route_appendix:
        write_route_appendix()
    if args.write_index:
        write_index()
    if args.print_routes:
        for route in extract_routes():
            print(f"{route.method}\t{route.path}\t{route.source}")

    errors = []
    errors.extend(audit_index())
    errors.extend(audit_links())
    errors.extend(audit_routes())
    warnings = audit_lines()

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        print(f"docs audit failed: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1
    print(f"docs audit passed: {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
