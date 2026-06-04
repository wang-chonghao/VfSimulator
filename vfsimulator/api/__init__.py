"""
Simulator input API package.

This package will host user-facing input adapters, including:
- CCE file input
- legacy JSON input
"""

from vfsimulator.api.program_api import predict_from_program

__all__ = ["predict_from_program"]
