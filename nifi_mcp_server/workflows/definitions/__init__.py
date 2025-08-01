"""
Workflow definitions for various NiFi operations.

This module contains the specific workflow implementations for
different types of NiFi operations and use cases.
"""

from .unguided_mimic import create_unguided_mimic_workflow

__all__ = ['create_unguided_mimic_workflow'] 