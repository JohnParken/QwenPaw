import importlib.util
from pathlib import Path
import unittest


class CompatibilityEntryTest(unittest.TestCase):
    def test_both_office_paths_load_current_v1_console(self):
        root = Path(__file__).resolve().parents[2]
        for name in ("office-agent-console", "qwenpaw-office-api-console"):
            with self.subTest(name=name):
                spec = importlib.util.spec_from_file_location(
                    "entry", root / name / "server.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertTrue(callable(module.main))
                self.assertEqual(module.module.ROOT.name, "server-console")
                self.assertIsNone(module.module.ROUTES.fullmatch("/api/v1/sessions"))
                self.assertIsNotNone(module.module.ROUTES.fullmatch("/v1/runs"))


if __name__ == "__main__":
    unittest.main()
