"""Local web application: browse a library, sync lyrics and fix timings by ear."""

from .server import AppServer, serve

__all__ = ["AppServer", "serve"]
