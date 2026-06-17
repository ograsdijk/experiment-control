import sys
import unittest

from experiment_control._driver.loading import import_class
from experiment_control.utils.module_loading import module_name_from_path
from tests._temp_utils import repo_temp_dir

_DRIVER_BODY = (
    "from ._helper import VALUE\n"
    "\n"
    "class MyDriver:\n"
    "    def connect(self):\n"
    "        pass\n"
    "    def disconnect(self):\n"
    "        pass\n"
    "    def value(self):\n"
    "        return VALUE\n"
)

_STANDALONE_BODY = (
    "class Solo:\n"
    "    def connect(self):\n"
    "        pass\n"
    "    def disconnect(self):\n"
    "        pass\n"
)


class ImportClassPackageAwareTests(unittest.TestCase):
    def test_relative_import_driver_loads(self) -> None:
        """A driver inside a package (with __init__.py chain) loads by dotted
        name so its relative imports resolve."""
        with repo_temp_dir(prefix="loadtest") as root:
            pkg = root / "drvpkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "_helper.py").write_text("VALUE = 42\n", encoding="utf-8")
            (pkg / "mydriver.py").write_text(_DRIVER_BODY, encoding="utf-8")

            self.assertEqual(
                module_name_from_path(pkg / "mydriver.py")[0], "drvpkg.mydriver"
            )
            try:
                cls = import_class(str(pkg / "mydriver.py"), "MyDriver")
                self.assertEqual(cls.__name__, "MyDriver")
                self.assertEqual(cls.__module__, "drvpkg.mydriver")
                # The relative `from ._helper import VALUE` resolved.
                self.assertEqual(cls().value(), 42)
            finally:
                for name in ("drvpkg.mydriver", "drvpkg._helper", "drvpkg"):
                    sys.modules.pop(name, None)
                if str(root) in sys.path:
                    sys.path.remove(str(root))

    def test_standalone_file_without_package_loads(self) -> None:
        """A driver file not inside a package falls back to file-path loading."""
        with repo_temp_dir(prefix="loadtest") as root:
            solo = root / "standalone_driver.py"
            solo.write_text(_STANDALONE_BODY, encoding="utf-8")

            # No __init__.py chain -> no inferable module name.
            self.assertEqual(module_name_from_path(solo), (None, None))
            cls = import_class(str(solo), "Solo")
            self.assertEqual(cls.__name__, "Solo")

    def test_missing_class_raises_import_error(self) -> None:
        with repo_temp_dir(prefix="loadtest") as root:
            solo = root / "standalone_driver.py"
            solo.write_text(_STANDALONE_BODY, encoding="utf-8")
            with self.assertRaises(ImportError):
                import_class(str(solo), "DoesNotExist")


if __name__ == "__main__":
    unittest.main()
