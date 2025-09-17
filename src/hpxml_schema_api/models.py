"""Data structures describing HPXML schema metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class ValidationRule:
    """Constraint derived from Schematron or project overrides."""

    message: str
    severity: str = "error"
    test: Optional[str] = None
    context: Optional[str] = None



@dataclass
class RuleNode:
    """Normalized representation of an HPXML element or field."""

    xpath: str
    name: str
    kind: str  # "section" or "field"
    data_type: Optional[str] = None
    min_occurs: Optional[int] = None
    max_occurs: Optional[str] = None
    repeatable: bool = False
    enum_values: List[str] = field(default_factory=list)
    description: Optional[str] = None
    validations: List[ValidationRule] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    children: List["RuleNode"] = field(default_factory=list)


    def iter_nodes(self) -> "List[RuleNode]":
        nodes: List[RuleNode] = [self]
        for child in self.children:
            nodes.extend(child.iter_nodes())
        return nodes

    def to_dict(self) -> dict:
        """Convert RuleNode to dictionary for API serialization."""
        return {
            "xpath": self.xpath,
            "name": self.name,
            "kind": self.kind,
            "data_type": self.data_type,
            "min_occurs": self.min_occurs,
            "max_occurs": self.max_occurs,
            "repeatable": self.repeatable,
            "enum_values": self.enum_values,
            "description": self.description,
            "validations": [
                {
                    "message": rule.message,
                    "severity": rule.severity,
                    "test": rule.test,
                    "context": rule.context,
                }
                for rule in self.validations
            ],
            "notes": self.notes,
            "children": [child.to_dict() for child in self.children],
        }
