"""LEco DevOps MCP server — agent access to the local ecosystem platform."""

from .config import Settings
from .server import SERVER_NAME, SERVER_VERSION, build_server

__version__ = SERVER_VERSION

__all__ = ["Settings", "build_server", "SERVER_NAME", "SERVER_VERSION", "__version__"]
