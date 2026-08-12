"""
Engine purity: apps/automations/engine/ must not depend on Django.

This is a MARKER FOR PHASE 1, not a passing test. The engine is meant to become a
framework-free execution core that two different consumers can share - the
multi-tenant AutoFlow API, and a single-user local orchestrator whose persistence
and scheduling look nothing like Django's. It cannot be shared while it reaches
into the ORM and into Celery on its own.

The rule is the same one CLAUDE.md section 4 already states for `core` ("core
imports nothing from outside"), with a new subject.

The test is marked expectedFailure, so:

  * today it does not break the suite - the violations below are known,
  * and the day Phase 1 makes it pass, unittest reports an *unexpected success*,
    which fails the run and forces whoever fixed it to delete the decorator.

A marker that cleans itself up.
"""
import ast
import pathlib
import unittest

from django.test import SimpleTestCase

# _escapes_engine is exercised directly below; importing it by name keeps the
# assertion readable.


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "apps" / "automations" / "engine"

# Importing any of these means the engine cannot run outside a configured Django
# project with a database and a broker behind it.
FORBIDDEN_ROOTS = {"django", "rest_framework", "celery"}


def _escapes_engine(path, level):
    """
    Does a relative import of this level climb out of engine/?

    Not simply `level >= 2`: `engine/actions/base.py` sits one package deeper, so
    `from ..exceptions import` there resolves to engine.exceptions and is perfectly
    fine, while the same statement in `engine/evaluator.py` lands in the Django app.
    Counting the levels without counting the depth flags the wrong lines - and a
    false positive here would send Phase 1 off to "fix" imports that are correct.
    """
    depth_below_engine = len(path.relative_to(ENGINE_DIR).parts) - 1
    return (level - 1) > depth_below_engine


def find_framework_imports(path):
    """Every framework dependency in one module, as (line, description) pairs."""
    tree = ast.parse(path.read_text(), filename=str(path))
    inside_engine = ENGINE_DIR in path.parents
    found = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_ROOTS:
                    found.append((node.lineno, f"import {alias.name}"))

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""

            if module.split(".")[0] in FORBIDDEN_ROOTS:
                found.append((node.lineno, f"from {module} import ..."))

            # A relative import that climbs past engine/ lands in the Django app;
            # `from ..models import Automation` is an ORM dependency wearing a
            # different hat.
            elif node.level and inside_engine and _escapes_engine(path, node.level):
                found.append((node.lineno, f"from {'.' * node.level}{module} import ..."))

    return found


def engine_modules():
    return sorted(p for p in ENGINE_DIR.rglob("*.py") if "__pycache__" not in p.parts)


class ImportScannerTests(SimpleTestCase):
    """Guards the scanner itself. Without this, a scanner that always returned []
    would make the purity test below an unexpected success for the wrong reason."""

    def test_the_scanner_sees_a_django_import_where_there_is_one(self):
        signals = REPO_ROOT / "apps" / "automations" / "signals.py"
        self.assertTrue(find_framework_imports(signals))

    def test_a_sibling_import_from_a_subpackage_is_not_a_violation(self):
        # engine/actions/base.py sits one package deeper, so `from ..exceptions`
        # there resolves to engine.exceptions and must not be flagged.
        base = ENGINE_DIR / "actions" / "base.py"
        relative = [d for _, d in find_framework_imports(base) if d.startswith("from .")]
        self.assertEqual(relative, [])

    def test_climbing_out_of_the_engine_is_a_violation(self):
        # ...while the same two dots in engine/evaluator.py reach the ORM.
        evaluator = ENGINE_DIR / "evaluator.py"
        self.assertIn(
            "from ..models import ...",
            [d for _, d in find_framework_imports(evaluator)],
        )

    def test_depth_is_counted_not_just_the_dots(self):
        self.assertFalse(_escapes_engine(ENGINE_DIR / "actions" / "base.py", 2))
        self.assertTrue(_escapes_engine(ENGINE_DIR / "actions" / "base.py", 3))
        self.assertTrue(_escapes_engine(ENGINE_DIR / "evaluator.py", 2))


class EnginePurityTests(SimpleTestCase):

    def test_the_engine_finds_its_own_modules(self):
        # Cheap guard against a moved directory silently emptying the check below.
        self.assertGreater(len(engine_modules()), 5)

    @unittest.expectedFailure
    def test_the_engine_does_not_import_django(self):
        violations = []

        for module in engine_modules():
            for lineno, description in find_framework_imports(module):
                violations.append(f"{module.relative_to(REPO_ROOT)}:{lineno}  {description}")

        self.assertEqual(
            violations,
            [],
            "engine/ still depends on the framework:\n  " + "\n  ".join(violations),
        )
