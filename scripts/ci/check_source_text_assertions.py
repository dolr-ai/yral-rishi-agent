"""Ratchet on tests that assert against our own source code as TEXT.

`assert "def foo" in src` passes whether or not `foo` works. The suite has
1,213 such assertions; ~510 of the file-reads behind them target `app/`, and
those are the dangerous ones — they stay green while behaviour breaks. Three
separate times in September a source-text test either hid a real break or had
its assertion edited to match one (PR #503 most plainly: the author updated
`assert 'collage_id: str | None = None' in src` to match the change that broke
video generation, and CI stayed green).

Reading a workflow YAML, a migration or a shell script as text is fine — the
file IS the artifact, and there is no behaviour to exercise. So this only
counts assertions whose text came from a `app/**.py` read.

This is a RATCHET, not a target: the number may fall, never rise. Converting a
source-text assertion to a behavioural one lowers the baseline; add the new
number to the baseline file in the same PR.
"""

import ast
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"
BASELINE_FILE = pathlib.Path(__file__).with_name("source-text-baseline.txt")

READS_A_FILE = re.compile(r"read_text\(|getsource\(|\.read\(\)")
TARGETS_APP = re.compile(r"""["'][^"']*\bapp/[^"']*\.py["']|getsource\(""")


def _app_source_vars(tree):
    """Local names holding the text of an `app/**.py` file.

    Both the direct form (`src = Path(...).read_text()`) and the helper form
    (`src = _read("app/services/x.py")`), which is the common one here and the
    reason a naive scan undercounts by half.
    """
    readers = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and READS_A_FILE.search(ast.unparse(fn))
    }
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = ast.unparse(node.value)
        called_a_reader = any(re.match(rf"{re.escape(r)}\s*\(", value) for r in readers)
        if not (READS_A_FILE.search(value) or called_a_reader):
            continue
        if not TARGETS_APP.search(value):
            continue  # a workflow/migration/script read — legitimate
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def count() -> tuple[int, dict[str, int]]:
    total, per_file = 0, {}
    for path in sorted(TESTS.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:
            continue
        names = _app_source_vars(tree)
        if not names:
            continue
        hits = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            test = ast.unparse(node.test)
            if any(
                re.search(
                    rf"\bin\s+{re.escape(n)}\b|{re.escape(n)}\.(count|index|find|splitlines)\(",
                    test,
                )
                for n in names
            ):
                hits += 1
        if hits:
            per_file[str(path.relative_to(REPO))] = hits
            total += hits
    return total, per_file


def main() -> int:
    current, per_file = count()
    baseline = int(BASELINE_FILE.read_text().split("#")[0].strip())
    print(f"source-text assertions against app/: {current} (baseline {baseline})")

    if current > baseline:
        print(f"\nFAIL: {current - baseline} new source-text assertion(s).")
        print("These pass whether or not the code works. Assert on behaviour")
        print("instead — call the thing and check what it returns or raises.")
        print("\nfiles contributing:")
        for name, n in sorted(per_file.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {n:>4}  {name}")
        return 1

    if current < baseline:
        print(f"\n{baseline - current} fewer than baseline — nice.")
        print(
            f"Lower the number in {BASELINE_FILE.relative_to(REPO)} to {current} in this PR."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
