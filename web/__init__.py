"""Web package for the evaluation harness.

The web package owns frontend assets, UI contract metadata, and the server-side
manifest used to resolve the live static root. Backend engines should not be
wired from here; the web layer should depend on ``memory.plugins.service``.
"""

from .package import WebPackageLayout, load_web_package

__all__ = ["WebPackageLayout", "load_web_package"]
