"""Combine structural (XSD) and validation (Schematron) rules.

This module provides thin orchestration helpers for producing a consolidated
`RuleNode` tree that includes both schema structure and attached validation
rules. Heavy-lifting is delegated to the XSD and Schematron parsers.

Removed feature: Earlier versions persisted JSON snapshots of the merged tree.
As of v0.3.0 the project favors dynamic parsing + caching for freshness.

Example:
    from pathlib import Path
    from hpxml_schema_api.merger import build_rules_tree

    tree = build_rules_tree(Path("HPXML.xsd"), Path("HPXML_Schematron.xml"))
    print("Root:", tree.name, "Total nodes:", len(tree.iter_nodes()))

Future enhancements (planned, not implemented):
* Intelligent merge of Schematron patterns that reference abstract contexts
* Deduplication / severity prioritization when multiple rules map to same node
* Optional provenance metadata (source pattern id, line numbers)
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
    """Parse XSD then (optionally) attach Schematron rules.

    Args:
        xsd_path: Path to HPXML XSD file.
        schematron_path: Optional path to Schematron file; if omitted only
            structural metadata is returned.
        root_element: Name of document root element inside the XSD.

    Returns:
        Root :class:`RuleNode` with any applicable validation rules attached.
    """
    tree = parse_xsd(Path(xsd_path), root_name=root_element)
    if schematron_path:
        tree = parse_schematron(Path(schematron_path), tree)
    return tree


def merge_rules(xsd_rules: RuleNode, schematron_rules: dict) -> RuleNode:
    """Placeholder for future granular rule merging logic.

    Current behavior simply returns ``xsd_rules`` unchanged because the
    attaching occurs in :func:`build_rules_tree`. In a richer implementation
    ``schematron_rules`` would represent a pre-processed mapping to be diffed
    or combined with existing node validations (handling duplicates, conflict
    resolution, and severity escalation).
    """
    return xsd_rules
