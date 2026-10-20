"""Run a trigger test and check whether a given except block's body actually executed.

Usage:
    python -B line_check.py --file <source.py> --line <except line> <test_script.py> [args...]

Prints the test's own output, then one final line:
    RUG LINE CHECK: {"executed": true, ...}

Exit codes: 0 = handler body ran, 1 = it did not run, 2 = no except clause at that line.
Standard library only, so it works in any project's Python without installing anything.
"""

import argparse
import ast
import json
import os
import runpy
import sys
import threading
import traceback


def norm(path):
    return os.path.normcase(os.path.realpath(path))


def find_handler_lines(source_file, line):
    with open(source_file, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_file)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.lineno == line:
            lines = set()
            for stmt in node.body:
                lines.update(range(stmt.lineno, stmt.end_lineno + 1))
            return sorted(lines)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="source file containing the except clause")
    parser.add_argument("--line", required=True, type=int, help="line number of the except clause")
    parser.add_argument("test_script")
    parser.add_argument("test_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    target = norm(args.file)
    handler_lines = find_handler_lines(args.file, args.line)
    if handler_lines is None:
        print("RUG LINE CHECK: " + json.dumps({
            "error": f"no except clause at {args.file}:{args.line}",
        }))
        sys.exit(2)

    executed = set()
    seen = {}  # co_filename -> is it the target file? realpath is slow, so ask once per file

    def local_trace(frame, event, arg):
        if event == "line":
            executed.add(frame.f_lineno)
        return local_trace

    def global_trace(frame, event, arg):
        # Only trace frames from the target file, so the test runs at near-normal speed.
        name = frame.f_code.co_filename
        is_target = seen.get(name)
        if is_target is None:
            is_target = seen[name] = norm(name) == target
        if is_target:
            return local_trace
        return None

    sys.argv = [args.test_script] + args.test_args
    test_error = None
    threading.settrace(global_trace)
    sys.settrace(global_trace)
    try:
        runpy.run_path(args.test_script, run_name="__main__")
    except SystemExit as e:
        if e.code not in (None, 0):
            test_error = f"SystemExit({e.code!r})"
    except BaseException:
        # The test itself crashed: report it in the result instead of hiding it.
        test_error = traceback.format_exc(limit=5)
    finally:
        sys.settrace(None)
        threading.settrace(None)

    hit = sorted(executed.intersection(handler_lines))
    print("RUG LINE CHECK: " + json.dumps({
        "executed": bool(hit),
        "handler": f"{args.file}:{args.line}",
        "handler_body_lines": handler_lines,
        "lines_hit": hit,
        "test_error": test_error,
    }))
    sys.exit(0 if hit else 1)


if __name__ == "__main__":
    main()
