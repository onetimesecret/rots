# src/rots/__init__.py

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rots")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"
