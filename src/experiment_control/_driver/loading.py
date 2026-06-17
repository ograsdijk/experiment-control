from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Protocol

from ..utils.module_loading import module_name_from_path


class Device(Protocol):
    """
    Minimal interface expected from a device object.

    Subclasses of DeviceRunner can define their own device interfaces.
    """

    def connect(self, *args: Any, **kwargs: Any) -> None: ...

    def disconnect(self) -> None: ...


def import_class(file_path: str | Path, class_name: str) -> type[Device]:
    """
    Import a class from a Python source file.

    Args:
        file_path: Path to a .py file (absolute or relative).
        class_name: Name of the class defined in that file.

    Returns:
        The class object.

    Notes:
    - When the file lives inside an importable package (an unbroken chain of
      ``__init__.py`` files), it is imported by its dotted module name so that
      relative imports inside the driver (e.g. ``from ._helpers import x``)
      resolve correctly. Otherwise it is loaded directly from the file path.
    - A minimal structural check is performed for the Device Protocol:
      the class must have attributes 'connect' and 'disconnect'.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Driver file does not exist: {str(path)!r}")
    if path.suffix.lower() != ".py":
        raise ValueError(f"Driver file must be a .py file: {str(path)!r}")
    if not class_name or not isinstance(class_name, str):
        raise ValueError("class_name must be a non-empty string")

    inferred_name, root = module_name_from_path(path)
    if inferred_name and root is not None:
        # Import as a proper package module so relative imports work.
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        module = importlib.import_module(inferred_name)
    else:
        # Standalone file: load directly. Create a unique module name to avoid
        # collisions if multiple files share a name.
        module_name = f"_centrex_driver_{path.stem}_{abs(hash(str(path)))}"

        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not create import spec for {str(path)!r}")

        module = importlib.util.module_from_spec(spec)

        # Register before exec so relative imports inside the loaded module can work.
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            # Avoid leaving a partially-imported module around
            sys.modules.pop(module_name, None)
            raise

    try:
        obj = getattr(module, class_name)
    except AttributeError as e:
        raise ImportError(
            f"Module loaded from {str(path)!r} has no attribute {class_name!r}"
        ) from e

    if not isinstance(obj, type):
        raise TypeError(
            f"{class_name!r} in {str(path)!r} did not resolve to a class (got {type(obj)!r})"
        )

    if not hasattr(obj, "connect"):
        raise TypeError(f"{class_name!r} is missing required attribute 'connect'")
    if not hasattr(obj, "disconnect"):
        raise TypeError(f"{class_name!r} is missing required attribute 'disconnect'")

    return obj  # type: ignore[return-value]
