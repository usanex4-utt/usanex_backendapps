"""
fix_awaits.py — run once from the project root:
    python fix_awaits.py

Scans backend/routes/*.py, finds every:
    x = get_current_user_from_request(...)
that's missing `await`, and:
  1) changes the enclosing `def`  →  `async def`
  2) inserts `await ` before the call
Safe to re-run (idempotent).
"""
import re
from pathlib import Path

ROUTES = Path("backend/routes")

CALL_RE = re.compile(
    r'^(?P<indent>\s*)(?P<target>[A-Za-z_]\w*)\s*=\s*(?!await )'
    r'(?P<call>get_current_user_from_request\s*\()'
)
DEF_RE = re.compile(r'^(?P<indent>\s*)(?P<async>async\s+)?def\s+(?P<name>\w+)\s*\(')


def enclosing_def(defs, line_idx):
    best = None
    for d in defs:
        if d["line"] < line_idx:
            if best is None or d["line"] > best["line"]:
                best = d
    return best


def main():
    total_files = 0
    total_fixes = 0

    for path in sorted(ROUTES.glob("*.py")):
        if path.name == "auth.py":
            continue  # defines the helper

        src = path.read_text(encoding="utf-8")
        lines = src.split("\n")

        defs = []
        for i, ln in enumerate(lines):
            m = DEF_RE.match(ln)
            if m:
                defs.append({
                    "line": i,
                    "indent": len(m.group("indent")),
                    "async": bool(m.group("async")),
                    "name": m.group("name"),
                })

        touched_defs = set()
        file_fixes = 0

        for i, ln in enumerate(lines):
            m = CALL_RE.match(ln)
            if not m:
                continue

            prefix = f"{m.group('indent')}{m.group('target')} = "
            rest = ln[len(prefix):]
            lines[i] = prefix + "await " + rest
            file_fixes += 1

            d = enclosing_def(defs, i)
            if d:
                touched_defs.add(d["line"])

        if file_fixes == 0:
            continue

        # make enclosing defs async
        for dl in touched_defs:
            m = DEF_RE.match(lines[dl])
            if m and not m.group("async"):
                lines[dl] = re.sub(r'\bdef\b', 'async def', lines[dl], count=1)

        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"✅ {path.name}: {file_fixes} await(s), {len(touched_defs)} def(s) -> async")
        total_files += 1
        total_fixes += file_fixes

    print(f"\nDone. {total_fixes} fixes across {total_files} file(s).")


if __name__ == "__main__":
    main()