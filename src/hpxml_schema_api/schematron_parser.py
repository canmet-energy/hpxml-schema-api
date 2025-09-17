"""Extract HPXML Schematron assertions into normalized metadata."""

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
    """Parse Schematron file to map contexts to validation rules."""

    def __init__(self, schematron_path: Path) -> None:
        self.path = Path(schematron_path)
        self.tree = ET.parse(self.path)
        self.root = self.tree.getroot()

    def iter_rules(self) -> Iterable[SchematronRule]:
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
        """Attach Schematron validations to the corresponding rule nodes."""

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
    yield node
    for child in node.children:
        yield from _iter_nodes(child)


def _normalize_xpath(xpath: str) -> str:
    return xpath.replace("h:", "").strip()


def parse_schematron(schematron_path: Path, root: RuleNode) -> RuleNode:
    parser = SchematronParser(Path(schematron_path))
    parser.attach_to_tree(root)
    return root
