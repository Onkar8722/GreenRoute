"""
GreenRoute - Smart Waste Management System
Core application package
"""

__version__ = "1.0.0"
__author__ = "GreenRoute Team"
__description__ = "IoT-enabled waste collection optimization platform"

from .app import create_app

__all__ = ["create_app"]
