"""R33 entrypoint regressions for issue #28. Authored, not locally run."""
import importlib.util
import pathlib
import subprocess
import sys
import unittest

TARGET = pathlib.Path(__file__).with_name("test_checker_r33.py")
LATER_TESTS = {
    "test_trailing_config_adopted",
    "test_non_trailing_config_ignored_degraded",
}


class R33EntrypointTest(unittest.TestCase):
    def test_direct_execution_runs_all_seven_including_later_class(self):
        result = subprocess.run(
            [sys.executable, str(TARGET), "-v"],
            cwd=str(TARGET.parent.parent), capture_output=True, text=True,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Ran 7 tests", output)
        self.assertIn("OK", output)
        for name in LATER_TESTS:
            self.assertIn(name, output)

    def test_import_discovery_includes_all_seven_and_later_class(self):
        spec = importlib.util.spec_from_file_location("r33_entrypoint_target", TARGET)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite = unittest.defaultTestLoader.loadTestsFromModule(module)
        self.assertEqual(suite.countTestCases(), 7)
        later = unittest.defaultTestLoader.loadTestsFromTestCase(
            module.R33CliConfigPositionTest,
        )
        self.assertEqual({test._testMethodName for test in later}, LATER_TESTS)


if __name__ == "__main__":
    unittest.main()
