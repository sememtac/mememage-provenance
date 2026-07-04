"""Guard: no module may use Python 3.12+ f-string syntax.

pyproject declares ``requires-python = ">=3.10"``, but the dev + CI Python
here is newer (3.14), so a 3.12-only construct compiles fine in tests and
ships anyway — then breaks on a user's 3.10/3.11. That's exactly what hid
``f"...{'\\u2026' if ...}"`` in server.py until the Windows desktop build
(Python 3.11) crashed with ``SyntaxError: f-string expression part cannot
include a backslash``.

The most common offender is a backslash inside an f-string ``{}``
expression (PEP 701, 3.12+). This scans every module's AST for it. It
runs on any Python >= 3.12 (3.10/3.11 would reject the file outright,
which is the failure we're preventing).
"""

import ast
import pathlib
import unittest

PKG = pathlib.Path(__file__).resolve().parent.parent / "mememage"


def _fstring_backslash_offenders():
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for value in node.values:
                    if isinstance(value, ast.FormattedValue):
                        seg = ast.get_source_segment(src, value)
                        if seg and "\\" in seg:
                            rel = path.relative_to(PKG.parent)
                            offenders.append(f"{rel}:{value.lineno}  {seg!r}")
    return offenders


class TestPy310FStringCompat(unittest.TestCase):
    def test_no_backslash_in_fstring_expression(self):
        offenders = _fstring_backslash_offenders()
        self.assertFalse(
            offenders,
            "Backslash inside an f-string {} expression is a SyntaxError "
            "before Python 3.12 (we target 3.10+). Move the value to a "
            "variable. Offenders:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
