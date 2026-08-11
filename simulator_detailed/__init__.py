import os as _os
import sys as _sys

_pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
_parent_dir = _os.path.dirname(_pkg_dir)
for _p in (_parent_dir, _pkg_dir):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from .core import Core
from .noc import Link, Router, NoC
from .architecture import Arch

for _name, _mod in list(_sys.modules.items()):
    if _mod is not None and hasattr(_mod, '__file__') and _mod.__file__:
        _mod_dir = _os.path.dirname(_os.path.abspath(_mod.__file__))
        if _mod_dir == _pkg_dir or _mod_dir.startswith(_pkg_dir + _os.sep):
            if not _name.startswith('simulator_detailed.'):
                _sys.modules[f'simulator_detailed.{_name}'] = _mod
