"""Combine schema and schematron rules into metadata trees.

Note: JSON snapshot generation has been removed as of v0.3.0.
Use the cached parser for dynamic schema access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .models import RuleNode
from .schematron_parser import parse_schematron
from .xsd_parser import parse_xsd


def build_rules_tree(
    xsd_path: Path,
    schematron_path: Optional[Path] = None,
    root_element: str = "HPXML",
) -> RuleNode:
    """Return combined rule tree for the supplied HPXML resources."""
    tree = parse_xsd(Path(xsd_path), root_name=root_element)
    if schematron_path:
        tree = parse_schematron(Path(schematron_path), tree)
    return tree


def merge_rules(xsd_rules: RuleNode, schematron_rules: dict) -> RuleNode:
    """Merge XSD rules with Schematron validation rules.

    Args:
        xsd_rules: Parsed XSD rule tree
        schematron_rules: Parsed Schematron rules (currently just returns xsd_rules)

    Returns:
        Combined rule tree with validation rules attached
    """
    # For now, just return the XSD rules
    # In a full implementation, this would merge schematron validations
    # into the appropriate nodes in the XSD tree
    return xsd_rules
