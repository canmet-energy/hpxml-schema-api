"""Core data structures for representing HPXML schema metadata.

These lightweight dataclasses are produced primarily by the XSD and Schematron
parsers and consumed by higher level layers (GraphQL schema generation, REST
endpoints, enhanced validation, form serialization, etc.). They intentionally
avoid framework dependencies so they can be serialized, cached, or transported
easily.

Overview:
        * ``ValidationRule`` encapsulates a single business or structural rule
            (most commonly originating from Schematron assert/report nodes but also
            usable for ad-hoc project overrides).
        * ``RuleNode`` forms a tree mirroring the logical HPXML element hierarchy.
            Each node captures structure (cardinality, data type, enumerations) and
            attaches any relevant ``ValidationRule`` instances.

Typical construction (simplified)::

        from hpxml_schema_api.models import RuleNode, ValidationRule

        attic_area_rule = ValidationRule(
                message="Attic area must be positive",
                severity="error",
                test="number(.) > 0",
                context="/HPXML/Building/BuildingDetails/Enclosure/Attic/Area"
        )

        attic_area = RuleNode(
                xpath="/HPXML/Building/BuildingDetails/Enclosure/Attic/Area",
                name="Area",
                kind="field",
                data_type="decimal",
                min_occurs=1,
                validations=[attic_area_rule]
        )

        attic = RuleNode(
                xpath="/HPXML/Building/BuildingDetails/Enclosure/Attic",
                name="Attic",
                kind="section",
                children=[attic_area]
        )

        # Flatten tree for indexing or inspection
        flat = [n.xpath for n in attic.iter_nodes()]
        # Serialize for API response
        payload = attic.to_dict()

Design notes:
        * ``RuleNode`` keeps children in a plain list for predictable order (matching
            discovery order in the source XSD) which helps deterministic diffing.
        * ``iter_nodes`` performs a depth-first traversal; this is adequate for moderate
            HPXML trees and avoids recursion complexity overhead from generators when
            simple materialization is sufficient.
        * ``to_dict`` produces stable keys to simplify client-side caching / hashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class ValidationRule:
    """Represents a single validation constraint.

    Attributes:
        message: Human-readable message displayed when the rule is violated (or informational).
        severity: One of ``error``, ``warning``, ``info`` (arbitrary strings permitted; higher layers may map severities).
        test: Original Schematron test / assert expression or custom logical condition (optional).
        context: XPath context in which the test should be evaluated (optional).

    Example:
        >>> rule = ValidationRule(message="Value must be positive", severity="error", test="number(.) > 0")
        >>> rule.severity
        'error'
    """

    message: str
    severity: str = "error"
    test: Optional[str] = None
    context: Optional[str] = None


@dataclass
class RuleNode:
    """Represents an HPXML element/field along with constraints and children.

    A ``RuleNode`` can represent either a structural grouping (``kind == 'section'``)
    or a leaf data field (``kind == 'field'``). For leaf nodes, ``data_type`` and
    ``enum_values`` describe primitive constraints. Cardinality is captured via
    ``min_occurs`` and ``max_occurs`` and a convenience ``repeatable`` boolean.

    Attributes:
        xpath: Absolute XPath identifying this node within the HPXML document.
        name: Local element/attribute name (friendly identifier).
        kind: ``'section'`` or ``'field'`` (other future categories possible).
        data_type: XSD base type (e.g., ``string``, ``decimal``) when applicable.
        min_occurs: Minimum occurrences (usually 0 or 1 for leaf fields).
        max_occurs: Maximum occurrences (string to allow 'unbounded').
        repeatable: Convenience flag inferred from cardinality (``max_occurs`` > 1 or unbounded).
        enum_values: Allowed enumeration values for the field (if restricted type).
        description: Optional human-readable description / annotation from XSD documentation.
        validations: List of attached :class:`ValidationRule` objects (Schematron + overrides).
        notes: Arbitrary textual notes (internal instrumentation or augmentation hooks).
        children: Nested ``RuleNode`` instances (empty for leaf fields).

    Example creation:
        >>> node = RuleNode(xpath="/HPXML/Some/Field", name="Field", kind="field", data_type="string")
        >>> node.repeatable
        False
        >>> len(node.children)
        0
    """

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
        """Return a depth-first list of this node and all descendants.

        Returns:
            List[RuleNode]: Concrete list materializing traversal order.

        Notes:
            This is intentionally not a generator to simplify repeated passes and
            JSON serialization. For large documents, a generator could be substituted
            if memory pressure becomes significant.

        Example:
            >>> parent = RuleNode(xpath="/A", name="A", kind="section")
            >>> child = RuleNode(xpath="/A/B", name="B", kind="field")
            >>> parent.children.append(child)
            >>> [n.xpath for n in parent.iter_nodes()]
            ['/A', '/A/B']
        """
        nodes: List[RuleNode] = [self]
        for child in self.children:
            nodes.extend(child.iter_nodes())
        return nodes

    def to_dict(self) -> dict:
        """Convert the node (recursively) into a JSON-serializable dictionary.

        Returns:
            dict: Primitive types only—safe for direct JSON encoding.

        Example:
            >>> node = RuleNode(xpath="/HPXML/Field", name="Field", kind="field")
            >>> "xpath" in node.to_dict()
            True
        """
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
