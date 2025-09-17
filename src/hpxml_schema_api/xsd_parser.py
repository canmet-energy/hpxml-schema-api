"""Utilities to parse HPXML XSD definitions into normalized metadata."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import RuleNode

XS_NS = "{http://www.w3.org/2001/XMLSchema}"


@dataclass
class SimpleType:
    name: str
    base: Optional[str]
    enumerations: List[str]


@dataclass
class ParserConfig:
    """Configuration for XSD parsing behavior."""
    max_extension_depth: int = 3  # Maximum depth for type extensions
    max_recursion_depth: int = 10  # Maximum overall recursion depth
    track_extension_metadata: bool = True  # Include extension chain info
    resolve_extension_refs: bool = False  # Whether to resolve extension element refs
    cache_resolved_refs: bool = True  # Cache resolved references


class XSDParser:
    """Parse HPXML XSD files into a tree of :class:`RuleNode`."""

    def __init__(self, xsd_path: Path, config: Optional[ParserConfig] = None) -> None:
        self.xsd_path = Path(xsd_path)
        self.config = config or ParserConfig()
        self.tree = ET.parse(self.xsd_path)
        self.root = self.tree.getroot()
        self.simple_types: Dict[str, SimpleType] = {}
        self.complex_types: Dict[str, ET.Element] = {}
        self.reference_cache: Dict[str, str] = {} if self.config.cache_resolved_refs else None
        self.extension_chains: Dict[str, List[str]] = {}  # Track inheritance chains
        self._index_simple_types()
        self._index_complex_types()
        if self.config.track_extension_metadata:
            self._index_extension_chains()

    def parse(self, root_name: str = "HPXML") -> RuleNode:
        element = self._find_element(root_name)
        if element is None:
            raise ValueError(f"Element '{root_name}' not found in {self.xsd_path}")
        return self._build_node(
            element, parent_xpath="", visited=set(), ref_chain=set(),
            depth=0, extension_depth=0
        )

    # ---------------- Internal helpers ---------------- #

    def _index_simple_types(self) -> None:
        for node in self.root.findall(f"{XS_NS}simpleType"):
            name = node.get("name")
            if not name:
                continue
            restriction = node.find(f"{XS_NS}restriction")
            base = restriction.get("base") if restriction is not None else None
            enumerations = []
            if restriction is not None:
                enumerations = [
                    enum.get("value")
                    for enum in restriction.findall(f"{XS_NS}enumeration")
                    if enum.get("value") is not None
                ]
            self.simple_types[name] = SimpleType(
                name=name, base=_local_name(base), enumerations=enumerations
            )

    def _index_complex_types(self) -> None:
        for node in self.root.findall(f"{XS_NS}complexType"):
            name = node.get("name")
            if not name:
                continue
            self.complex_types[name] = node

    def _index_extension_chains(self) -> None:
        """Build inheritance chains for complex types."""
        for type_name, type_elem in self.complex_types.items():
            chain = self._get_extension_chain(type_elem, type_name)
            if chain:
                self.extension_chains[type_name] = chain

    def _get_extension_chain(self, type_elem: ET.Element, type_name: str,
                           visited: Optional[Set[str]] = None) -> List[str]:
        """Get the inheritance chain for a complex type."""
        if visited is None:
            visited = set()

        if type_name in visited:
            return []  # Circular reference detected

        visited.add(type_name)

        complex_content = type_elem.find(f"{XS_NS}complexContent")
        if complex_content is not None:
            extension = complex_content.find(f"{XS_NS}extension")
            if extension is not None:
                base = extension.get("base")
                if base:
                    base_name = _local_name(base)
                    if base_name in self.complex_types:
                        parent_chain = self._get_extension_chain(
                            self.complex_types[base_name],
                            base_name,
                            visited.copy()
                        )
                        return [base_name] + parent_chain
        return []

    def _find_element(self, name: str) -> Optional[ET.Element]:
        for element in self.root.findall(f"{XS_NS}element"):
            if element.get("name") == name:
                return element
        return None

    def _build_node(
        self,
        element: ET.Element,
        parent_xpath: str,
        visited: set[str],
        ref_chain: set[str],
        depth: int = 0,
        extension_depth: int = 0,
    ) -> RuleNode:
        # Check depth limits
        if depth > self.config.max_recursion_depth:
            return RuleNode(
                xpath=f"{parent_xpath}/...",
                name="[depth_limit_reached]",
                kind="section",
                notes=[f"max_depth_{self.config.max_recursion_depth}_exceeded"],
                description=f"Maximum recursion depth ({self.config.max_recursion_depth}) exceeded",
            )

        element, ref_name, added_to_chain = self._resolve_reference(element, ref_chain)
        name = element.get("name")
        if not name:
            raise ValueError("Encountered anonymous element in XSD")

        xpath = f"{parent_xpath}/{name}" if parent_xpath else f"/{name}"
        if xpath in visited:
            return RuleNode(
                xpath=xpath,
                name=name,
                kind="section",
                notes=["recursive_reference"],
            )
        visited.add(xpath)
        min_occurs = _parse_occurs(element.get("minOccurs"))
        raw_max_occurs = element.get("maxOccurs", "1")
        max_occurs = raw_max_occurs
        repeatable = raw_max_occurs == "unbounded" or (
            raw_max_occurs.isdigit() and int(raw_max_occurs) > 1
        )

        simple_type = element.find(f"{XS_NS}simpleType")
        complex_type = element.find(f"{XS_NS}complexType")
        element_type = element.get("type")

        if name == "extension" or (ref_name == "extension" and not self.config.resolve_extension_refs):
            visited.discard(xpath)
            if ref_name and added_to_chain:
                ref_chain.discard(ref_name)

            # Add metadata about what extensions are possible here
            extension_notes = ["extension_point"]
            if self.config.track_extension_metadata:
                extension_notes.append("allows_custom_data")
                if extension_depth > 0:
                    extension_notes.append(f"extension_depth_{extension_depth}")

            return RuleNode(
                xpath=xpath,
                name=name,
                kind="section",
                min_occurs=min_occurs,
                max_occurs=raw_max_occurs,
                repeatable=repeatable,
                notes=extension_notes,
                description="Extension point for custom data",
            )

        # Check extension depth for complex types
        type_name = element.get("type")
        if type_name and self.config.track_extension_metadata:
            type_local = _local_name(type_name)
            if type_local in self.extension_chains:
                chain = self.extension_chains[type_local]
                new_extension_depth = extension_depth + len(chain)
                if new_extension_depth > self.config.max_extension_depth:
                    visited.discard(xpath)
                    if ref_name and added_to_chain:
                        ref_chain.discard(ref_name)
                    truncated_chain = chain[:2] if len(chain) > 2 else chain
                    return RuleNode(
                        xpath=xpath,
                        name=name,
                        kind="section",
                        min_occurs=min_occurs,
                        max_occurs=raw_max_occurs,
                        repeatable=repeatable,
                        notes=[
                            "extension_chain_truncated",
                            f"inherits_from_{len(chain)}_types",
                            f"base_types: {', '.join(truncated_chain)}"
                        ],
                        description=f"Complex type with {len(chain)}-level inheritance (truncated at depth {self.config.max_extension_depth})",
                    )

        if simple_type is not None:
            data_type, enums = self._parse_inline_simple_type(simple_type)
            visited.discard(xpath)
            if ref_name and added_to_chain:
                ref_chain.discard(ref_name)
            return RuleNode(
                xpath=xpath,
                name=name,
                kind="field",
                data_type=data_type,
                enum_values=enums,
                min_occurs=min_occurs,
                max_occurs=raw_max_occurs,
                repeatable=repeatable,
            )

        if complex_type is not None or self._is_complex_type(element_type):
            # Calculate new extension depth
            new_ext_depth = extension_depth
            if element_type and self.config.track_extension_metadata:
                type_local = _local_name(element_type)
                if type_local in self.extension_chains:
                    new_ext_depth += len(self.extension_chains[type_local])

            children = self._parse_complex_content(
                complex_type if complex_type is not None else self.complex_types.get(_local_name(element_type)),
                parent_xpath=xpath,
                visited=visited,
                ref_chain=ref_chain,
                depth=depth + 1,
                extension_depth=new_ext_depth,
            )
            visited.discard(xpath)
            if ref_name and added_to_chain:
                ref_chain.discard(ref_name)
            return RuleNode(
                xpath=xpath,
                name=name,
                kind="section",
                min_occurs=min_occurs,
                max_occurs=raw_max_occurs,
                repeatable=repeatable,
                children=children,
            )

        # Otherwise treat as field referencing a simple/builtin type
        data_type, enums = self._resolve_type(element_type)
        visited.discard(xpath)
        if ref_name and added_to_chain:
            ref_chain.discard(ref_name)
        return RuleNode(
            xpath=xpath,
            name=name,
            kind="field",
            data_type=data_type,
            enum_values=enums,
            min_occurs=min_occurs,
            max_occurs=raw_max_occurs,
            repeatable=repeatable,
        )

    def _parse_inline_simple_type(self, node: ET.Element) -> tuple[str, List[str]]:
        restriction = node.find(f"{XS_NS}restriction")
        if restriction is None:
            return "string", []
        base = _local_name(restriction.get("base")) or "string"
        enums = [
            enum.get("value")
            for enum in restriction.findall(f"{XS_NS}enumeration")
            if enum.get("value") is not None
        ]
        if not enums:
            enums = self._collect_enum_values(base)
        return base, enums

    def _parse_complex_content(
        self,
        node: Optional[ET.Element],
        parent_xpath: str,
        visited: set[str],
        ref_chain: set[str],
        depth: int = 0,
        extension_depth: int = 0,
    ) -> List[RuleNode]:
        if node is None:
            return []
        sequence = node.find(f"{XS_NS}sequence")
        choice = node.find(f"{XS_NS}choice")
        all_node = node.find(f"{XS_NS}all")
        children: List[RuleNode] = []
        containers = [n for n in [sequence, choice, all_node] if n is not None]
        if not containers:
            # Complex type with no child elements (e.g., attributes only)
            return children
        for container in containers:
            for child in container.findall(f"{XS_NS}element"):
                child_kind = "choice" if container is choice else "sequence"
                child_node = self._build_node(
                    child, parent_xpath, visited, ref_chain,
                    depth=depth, extension_depth=extension_depth
                )
                if child_kind == "choice":
                    # mark grouping via note for UI consumption
                    child_node.notes.append("choice")
                if child_node not in children:
                    children.append(child_node)
        return children

    def _resolve_type(self, type_name: Optional[str]) -> tuple[str, List[str]]:
        if not type_name:
            return "string", []
        local_type = _local_name(type_name)
        enums = self._collect_enum_values(local_type)
        base = self.simple_types.get(local_type)
        data_type = local_type
        if base and base.base:
            data_type = base.base
        return data_type, enums

    def _is_complex_type(self, type_name: Optional[str]) -> bool:
        if not type_name:
            return False
        return _local_name(type_name) in self.complex_types

    def _collect_enum_values(self, type_name: Optional[str]) -> List[str]:
        values: List[str] = []
        seen: set[str] = set()
        current = _local_name(type_name)
        while current:
            if current in seen:
                break
            seen.add(current)
            simple = self.simple_types.get(current)
            if simple is None:
                break
            if simple.enumerations:
                values.extend(simple.enumerations)
            current = simple.base
        return values

    def _resolve_reference(
        self, element: ET.Element, ref_chain: set[str]
    ) -> tuple[ET.Element, Optional[str], bool]:
        ref = element.get("ref")
        if not ref:
            return element, None, False

        referenced_name = _local_name(ref)
        if not referenced_name:
            return element, None, False

        if referenced_name in ref_chain:
            placeholder = ET.Element(element.tag)
            placeholder.attrib.update(element.attrib)
            placeholder.set("name", referenced_name)
            placeholder.attrib.pop("ref", None)
            return placeholder, referenced_name, False

        referenced_element = self._find_element(referenced_name)
        if referenced_element is None:
            return element, referenced_name, False

        ref_chain.add(referenced_name)

        if self.reference_cache is not None and referenced_name in self.reference_cache:
            cached_xml = self.reference_cache[referenced_name]
            cached_element = ET.fromstring(cached_xml)
            cached_element.attrib.pop("ref", None)
            return cached_element, referenced_name, False

        resolved = ET.Element(referenced_element.tag)
        resolved.attrib.update(referenced_element.attrib)
        resolved.attrib.pop("ref", None)

        for child in referenced_element:
            if child.tag in {f"{XS_NS}simpleType", f"{XS_NS}complexType"}:
                resolved.append(deepcopy(child))

        if self.reference_cache is not None:
            self.reference_cache[referenced_name] = ET.tostring(resolved, encoding="unicode")

        # Override occurrence constraints from referencing element
        for attr in ("minOccurs", "maxOccurs"):
            value = element.get(attr)
            if value is not None:
                resolved.set(attr, value)

        resolved_element = ET.fromstring(ET.tostring(resolved, encoding="unicode"))
        resolved_element.attrib.pop("ref", None)
        return resolved_element, referenced_name, True


def _parse_occurs(value: Optional[str]) -> Optional[int]:
    if value is None:
        return 1
    if value.isdigit():
        return int(value)
    return None


def _local_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if ":" in value:
        return value.split(":", 1)[1]
    return value


def parse_xsd(xsd_path: Path, root_name: str = "HPXML",
             config: Optional[ParserConfig] = None) -> RuleNode:
    """Convenience wrapper returning the rule tree rooted at ``root_name``.

    Args:
        xsd_path: Path to XSD schema file
        root_name: Name of root element to parse from
        config: Optional parser configuration for depth limits and extension handling

    Returns:
        RuleNode tree starting from root_name
    """
    return XSDParser(Path(xsd_path), config=config).parse(root_name=root_name)
