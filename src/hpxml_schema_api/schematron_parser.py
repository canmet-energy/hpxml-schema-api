"""Extract HPXML Schematron assertions into normalized metadata.

This module ingests an HPXML Schematron file and attaches its assertion /
report rules to the in‑memory :class:`RuleNode` tree previously produced from
the XSD. The result is a unified representation combining structural schema
information (elements, datatypes, enumerations) with business / validation
logic (Schematron rules) for downstream REST, GraphQL, or MCP consumption.

Features:
* Iterates patterns → rules → (assert|report) producing normalized
    :class:`SchematronRule` objects
* Normalizes context XPath by stripping any namespace prefixes (e.g. ``h:``)
* Appends :class:`ValidationRule` objects onto matching :class:`RuleNode`
    instances, preserving original test expression and severity

Severity mapping:
* If an ``@role`` is absent on ``sch:assert`` nodes we default to ``error``
* If an ``@role`` is absent on ``sch:report`` nodes we default to ``warn``

Example:
        from pathlib import Path
        from hpxml_schema_api.xsd_parser import parse_xsd
        from hpxml_schema_api.schematron_parser import parse_schematron

        xsd_root = parse_xsd(Path("HPXML.xsd"))
        combined_root = parse_schematron(Path("HPXML_Schematron.xml"), xsd_root)

        # List validations attached to a field
        building_id = next(n for n in combined_root.iter_nodes() if n.name == "BuildingID")
        for v in building_id.validations:
                print(v.severity, v.message)

Limitations:
* Does not currently evaluate XPath – it only stores the test expressions.
* Namespace removal strategy is simplistic; adjust `_normalize_xpath` if
    full namespace preservation becomes necessary.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .models import RuleNode, ValidationRule

SCH_NS = {
    "sch": "http://purl.oclc.org/dsdl/schematron",
    "svrl": "http://purl.oclc.org/dsdl/svrl",
}


@dataclass
class SchematronRule:
    context: str
    message: str
    test: str
    severity: str


class SchematronParser:
    """Parse a Schematron file and expose its rules.

    Args:
        schematron_path: Path to the Schematron XML file.

    The parser performs a shallow extraction converting each ``sch:assert`` or
    ``sch:report`` into a :class:`SchematronRule` with a simplified severity
    (lower‑cased) and trimmed message text.
    """

    def __init__(self, schematron_path: Path) -> None:
        self.path = Path(schematron_path)
        self.tree = ET.parse(self.path)
        self.root = self.tree.getroot()

    def iter_rules(self) -> Iterable[SchematronRule]:
        """Yield each Schematron rule as a :class:`SchematronRule`.

        Returns:
            Iterator of normalized rules preserving context XPath and test
            expression. Messages are stripped; severity is lower‑cased.

        Example:
            parser = SchematronParser(Path("hpxml_rules.sch"))
            for rule in parser.iter_rules():
                print(rule.context, rule.severity, rule.test)
        """
        for pattern in self.root.findall("sch:pattern", namespaces=SCH_NS):
            for rule in pattern.findall("sch:rule", namespaces=SCH_NS):
                context = rule.get("context")
                if not context:
                    continue
                for assert_node in rule.findall("sch:assert", namespaces=SCH_NS):
                    yield SchematronRule(
                        context=context,
                        message=(assert_node.text or "").strip(),
                        test=assert_node.get("test", ""),
                        severity=assert_node.get("role", "ERROR").lower(),
                    )
                for report_node in rule.findall("sch:report", namespaces=SCH_NS):
                    yield SchematronRule(
                        context=context,
                        message=(report_node.text or "").strip(),
                        test=report_node.get("test", ""),
                        severity=report_node.get("role", "WARN").lower(),
                    )

    def attach_to_tree(self, root: RuleNode) -> None:
        """Attach parsed validation rules to matching nodes in a rule tree.

        Args:
            root: Root :class:`RuleNode` of the existing schema tree.

        Process:
            1. Create a lookup of normalized XPath → :class:`RuleNode`
            2. Iterate extracted rules and append a :class:`ValidationRule`
               to the target node's ``validations`` list if found.

        Example:
            parser = SchematronParser(Path("rules.sch"))
            parser.attach_to_tree(root_node)
            total_rules = sum(len(n.validations) for n in root_node.iter_nodes())
            print("Attached", total_rules, "rules")
        """

        context_map = {}
        for node in _iter_nodes(root):
            context_map[_normalize_xpath(node.xpath)] = node

        for rule in self.iter_rules():
            normalized = _normalize_xpath(rule.context)
            target = context_map.get(normalized)
            if target is None:
                continue
            target.validations.append(
                ValidationRule(
                    message=rule.message,
                    severity=rule.severity,
                    test=rule.test,
                    context=rule.context,
                )
            )


def _iter_nodes(node: RuleNode) -> Iterable[RuleNode]:
    """Depth-first traversal generator for :class:`RuleNode` trees."""
    yield node
    for child in node.children:
        yield from _iter_nodes(child)


def _normalize_xpath(xpath: str) -> str:
    """Normalize an XPath (strip HPXML namespace prefixes).

    Current strategy removes leading ``h:`` occurrences. If future variants
    add multiple namespace prefixes, adjust here instead of scattering logic
    throughout the code base.
    """
    return xpath.replace("h:", "").strip()


def parse_schematron(schematron_path: Path, root: RuleNode) -> RuleNode:
    """Parse a Schematron file and attach rules to an existing tree.

    Args:
        schematron_path: Path to the Schematron XML file.
        root: Existing :class:`RuleNode` root produced from XSD parsing.

    Returns:
        The same ``root`` instance with validation rules appended in-place.

    Example:
        combined = parse_schematron(Path("rules.sch"), xsd_root)
        print(sum(len(n.validations) for n in combined.iter_nodes()))
    """
    parser = SchematronParser(Path(schematron_path))
    parser.attach_to_tree(root)
    return root
