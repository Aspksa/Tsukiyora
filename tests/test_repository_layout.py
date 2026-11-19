"""Repository layout guard: internal source belongs to topic packages.

No third party dependencies. The BAT entrypoint and restore script remain root.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    "academy": "academy_curriculum academy_experiments academy_metrics academy_practice academy_rollback soul_academy",
    "cognition": "cognitive_core cognitive_evolution cognitive_next cognitive_supervisor7 reasoning reflective_optimization reflective_recall self_improvement unified_cognitive skill_forge growth_quality",
    "memory": "atlas_materializer brain_atlas layered_memory memory_quality memory_store migration_guard precision_memory semantic_groups vector_memory",
    "cloud": "cloudru resource_guard stream_runtime",
    "runtime": "reliability maintenance",
    "personality": "persona_arc persona_continuity soul_growth living_personality8 living_dialogue9 experience_engine experience_skills conversation_memory topic_recall",
    "documents": "contract_insights document_parser evidence_review local_documents rag_fusion rag_pipeline",
}


class RepositoryLayoutTests(unittest.TestCase):
    def test_only_entrypoints_remain_in_root(self):
        self.assertEqual(
            {p.name for p in ROOT.glob("*.py")},
            {"launcher.py", "restore_memory.py"},
        )
        self.assertTrue((ROOT / "Tsukiyora.bat").is_file())
        self.assertTrue((ROOT / "soul" / "personality.json").is_file())

    def test_packages_and_all_source_modules_are_importable(self):
        for category, names in MODULES.items():
            folder = ROOT / "tsukiyora" / category
            self.assertTrue((folder / "__init__.py").is_file(), category)
            for module in names.split():
                with self.subTest(module=module):
                    self.assertTrue((folder / (module + ".py")).is_file())
                    imported = importlib.import_module(
                        "tsukiyora." + category + "." + module
                    )
                    self.assertTrue(hasattr(imported, "__file__"))

    def test_no_unqualified_legacy_imports(self):
        names = {name for labels in MODULES.values()
                 for name in labels.split()}
        for file in list((ROOT / "tsukiyora").rglob("*.py")) + [
            ROOT / "launcher.py", ROOT / "restore_memory.py"
        ] + list((ROOT / "tests").glob("*.py")) + list(
            (ROOT / "scripts").glob("*.py")
        ):
            with self.subTest(file=str(file.relative_to(ROOT))):
                parsed = ast.parse(file.read_text(encoding="utf-8"))
                for node in ast.walk(parsed):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn(alias.name, names,
                                             "Use qualified package imports")
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        self.assertNotIn(node.module, names,
                                         "Use qualified package imports")


if __name__ == "__main__":
    unittest.main()
