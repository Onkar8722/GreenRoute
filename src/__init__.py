"""
GreenRoute - Smart Waste Management System
Core application package
"""

__version__ = "1.0.0"
__author__ = "GreenRoute Team"
__description__ = "IoT-enabled waste collection optimization platform"

def create_app():
    """Create the Flask application without importing it during package startup."""
    from .app import create_app as create_application

    return create_application()

__all__ = ["create_app"]
