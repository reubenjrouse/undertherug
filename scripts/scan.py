"""Find fallback candidates in Python files and cut out a short snippet for each.

Usage:
    python -B scan.py --root <project root> [--lines <file>:<a>-<b> ...] <file.py> [<file.py> ...]
    python -B scan.py --root <project root> --all

Finds:
    - except blocks whose body doesn't raise
    - .get(key, default) and getattr(obj, name, default) calls

For each candidate it keeps the try/except (or the line around the call) and up to
three places that call the enclosing function, so a scorer can judge it without
reading whole files. Callers are found by parsing every project file (skipping
virtualenvs and build output); files that can't be parsed for that are listed under
"callers_not_searched".

Writes the candidates to a new temp directory, split into batch files of 25, and
prints a short JSON summary with the directory and batch paths. Files that can't be
read or parsed are listed under "unreadable", never skipped silently.

Standard library only, so it works in any project's Python without installing anything.
"""

import argparse
import ast
import json
import os
import tempfile

BATCH_SIZE = 25
MAX_TRY_LINES = 12
MAX_HANDLER_LINES = 12
MAX_CALLERS = 3
CALLER_LINES_AFTER = 8
SKIP_DIRS = {".git", ".venv", "venv", "site-packages", "node_modules", "build", "dist", "__pycache__"}


def read_source(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def clip(lines, start, end, limit):
    """Lines start..end (1-based, inclusive), cut to `limit` lines with a marker."""
    chunk = lines[start - 1:end]
    if len(chunk) > limit:
        chunk = chunk[:limit] + [f"    ... ({len(lines[start - 1:end]) - limit} more lines)"]
    return chunk


def numbered(lines, first_lineno):
    return "\n".join(f"{first_lineno + i:>5}  {line}" for i, line in enumerate(lines))


def parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def enclosing(node, parents, kinds):
    while node in parents:
        node = parents[node]
        if isinstance(node, kinds):
            return node
    return None


def except_snippet(handler, try_node, func, lines):
    parts = []
    if func is not None:
        parts.append(numbered([lines[func.lineno - 1]], func.lineno))
        if try_node.lineno - func.lineno > 1:
            parts.append("       ...")
    try_end = try_node.body[-1].end_lineno
    parts.append(numbered(clip(lines, try_node.lineno, try_end, MAX_TRY_LINES), try_node.lineno))
    if handler.lineno - try_end > 1:
        parts.append("       ...")
    parts.append(numbered(clip(lines, handler.lineno, handler.end_lineno, MAX_HANDLER_LINES), handler.lineno))
    return "\n".join(parts)


def call_snippet(call, func, lines):
    start = max(1, call.lineno - 3)
    end = min(len(lines), call.end_lineno + 3)
    parts = []
    if func is not None and func.lineno < start:
        parts.append(numbered([lines[func.lineno - 1]], func.lineno))
        if start - func.lineno > 1:
            parts.append("       ...")
    parts.append(numbered(lines[start - 1:end], start))
    return "\n".join(parts)


def is_test_file(rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    return any(p in ("tests", "test") for p in parts[:-1]) or parts[-1].startswith("test_")


def project_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class CallerIndex:
    """Finds where a function is called, across the whole project.

    Parses each project file once and records every call by name (`name(...)` or
    `obj.name(...)`), so text in comments and docstrings never counts as a call.
    """

    def __init__(self, root):
        self.root = root
        self.calls = None   # name -> [(path, line)]
        self.lines = {}     # path -> source lines
        self.unreadable = []

    def load(self):
        self.calls = {}
        for path in project_files(self.root):
            try:
                source = read_source(path)
                tree = ast.parse(source, filename=path)
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as e:
                self.unreadable.append({"file": os.path.relpath(path, self.root), "error": f"{type(e).__name__}: {e}"})
                continue
            self.lines[path] = source.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                    if name:
                        self.calls.setdefault(name, []).append((path, node.lineno))

    def find(self, name):
        if self.calls is None:
            self.load()
        # Real callers before test files: tests rarely show what the value does to the program.
        sites = sorted(self.calls.get(name, []), key=lambda s: is_test_file(os.path.relpath(s[0], self.root)))
        found = []
        for path, line in sites[:MAX_CALLERS]:
            lines = self.lines[path]
            # More lines after the call than before: what happens to the value is what matters.
            start, end = max(1, line - 2), min(len(lines), line + CALLER_LINES_AFTER)
            found.append({
                "file": os.path.relpath(path, self.root),
                "line": line,
                "code": numbered(lines[start - 1:end], start),
            })
        return found


def parse_ranges(specs, root):
    ranges = {}
    for spec in specs:
        path, _, span = spec.rpartition(":")
        a, _, b = span.partition("-")
        key = os.path.normcase(os.path.abspath(os.path.join(root, path)))
        ranges.setdefault(key, []).append((int(a), int(b or a)))
    return ranges


def in_ranges(ranges, path, start, end):
    key = os.path.normcase(os.path.abspath(path))
    if key not in ranges:
        return False
    return any(a <= end and start <= b for a, b in ranges[key])


def scan_file(path, root, callers, ranges):
    source = read_source(path)
    tree = ast.parse(source, filename=path)
    lines = source.splitlines()
    parents = parent_map(tree)
    funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
    rel = os.path.relpath(path, root)
    found = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if any(isinstance(n, ast.Raise) for stmt in node.body for n in ast.walk(stmt)):
                continue
            span = (node.lineno, node.end_lineno)
            kind = "except " + (ast.unparse(node.type) if node.type is not None else "(bare)")
            snippet = except_snippet(node, parents[node], enclosing(node, parents, funcs), lines)
        elif isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "get" and len(node.args) == 2)
            or (isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) == 3)
        ):
            span = (node.lineno, node.end_lineno)
            kind = "default: " + ast.unparse(node)[:80]
            snippet = call_snippet(node, enclosing(node, parents, funcs), lines)
        else:
            continue

        if ranges is not None and not in_ranges(ranges, path, *span):
            continue
        func = enclosing(node, parents, funcs)
        found.append({
            "file": rel,
            "line": node.lineno,
            "function": func.name if func else "<module>",
            "kind": kind,
            "snippet": snippet,
            "callers": callers.find(func.name) if func else [],
        })
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="project root, for relative paths and finding callers")
    parser.add_argument("--lines", action="append", default=[],
                        help="only keep candidates touching <file>:<a>-<b> (repeatable); for recent-changes audits")
    parser.add_argument("--all", action="store_true",
                        help="scan every .py file under --root, skipping virtualenvs and build output")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    if not args.all and not args.files:
        parser.error("give files to scan, or --all")

    root = os.path.abspath(args.root)
    if args.all:
        args.files = sorted(project_files(root))
    ranges = parse_ranges(args.lines, root) if args.lines else None
    callers = CallerIndex(root)
    candidates, unreadable = [], []

    for path in args.files:
        try:
            candidates.extend(scan_file(os.path.abspath(path), root, callers, ranges))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as e:
            unreadable.append({"file": path, "error": f"{type(e).__name__}: {e}"})

    candidates.sort(key=lambda c: (c["file"], c["line"]))
    for i, c in enumerate(candidates, 1):
        c["id"] = i

    out_dir = tempfile.mkdtemp(prefix="rug-scan-")
    batches = []
    for n, start in enumerate(range(0, len(candidates), BATCH_SIZE), 1):
        batch_path = os.path.join(out_dir, f"batch_{n:03d}.json")
        with open(batch_path, "w", encoding="utf-8") as f:
            json.dump(candidates[start:start + BATCH_SIZE], f, indent=1)
        batches.append(batch_path)
    index_path = os.path.join(out_dir, "candidates.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump([{k: c[k] for k in ("id", "file", "line", "function", "kind")} for c in candidates], f, indent=1)

    print(json.dumps({
        "files_scanned": len(args.files) - len(unreadable),
        "candidates": len(candidates),
        "out_dir": out_dir,
        "index": index_path,
        "batches": batches,
        "unreadable": unreadable,
        "callers_not_searched": callers.unreadable,
    }, indent=1))


if __name__ == "__main__":
    main()
