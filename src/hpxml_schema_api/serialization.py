"""HPXML fragment serialization utilities.

This module supplies helpers for converting between:
* In-memory `RuleNode` derived, form-oriented field collections
* Lightweight fragment dictionaries for API payloads
* XML Elements suitable for persistence

Use cases:
        1. UI requests a partial subtree (e.g., one building)
        2. Client edits values in a form
        3. Application validates & persists back to XML disk or another service

Example round-trip:
        from pathlib import Path
        from hpxml_schema_api.xsd_parser import parse_xsd
        from hpxml_schema_api.serialization import HPXMLSerializer

        root = parse_xsd(Path("HPXML.xsd"))
        serializer = HPXMLSerializer(root)
        fragment = serializer.create_fragment("/HPXML/Building")
        fragment.fields[0].value = "B123"
        xml_elem = serializer.fragment_to_xml(fragment)
        fragment2 = serializer.xml_to_fragment(xml_elem, "/HPXML/Building")

Design notes:
* Attribute support is minimal—attributes are preserved if present but not
    inferred from the schema (HPXML usage is predominantly element-centric).
* Validation here is structural and datatype/enumeration focused; business
    rules remain the responsibility of the enhanced validation subsystem.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import RuleNode


@dataclass
class HPXMLField:
    """Concrete value + metadata for a single field.

    Attributes:
        xpath: Absolute XPath to the field within an HPXML document.
        value: String representation of the value (None if unset).
        attributes: Element attribute name→value mapping (if any).
        notes: Informational annotations propagated from schema nodes.
        validation_errors: Collected validation error messages (if validated).
    """

    xpath: str
    value: Optional[str] = None
    attributes: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)


@dataclass
class HPXMLFragment:
    """Editable set of HPXML fields rooted at a specific subtree.

    Provides timestamps + optional metadata bag for client uses (UI hints,
    provenance, etc.).
    """

    root_xpath: str
    fields: List[HPXMLField] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = "4.0"
    created_at: Optional[str] = None
    modified_at: Optional[str] = None

    def __post_init__(self):
        """Set timestamps if not provided."""
        now = datetime.now().isoformat()
        if self.created_at is None:
            self.created_at = now
        self.modified_at = now


class HPXMLSerializer:
    """Serialize / deserialize HPXML fragments for form editing workflows.

    Args:
        rule_node: Root rule tree providing structural + enumeration metadata.
    """

    def __init__(self, rule_node: RuleNode):
        """Initialize with a rule node defining the structure."""
        self.rule_node = rule_node
        self.field_map = self._build_field_map(rule_node)

    def _build_field_map(self, node: RuleNode, prefix: str = "") -> Dict[str, RuleNode]:
        """Build a map of xpaths to rule nodes for quick lookup."""
        field_map = {}
        current_path = f"{prefix}/{node.name}" if prefix else f"/{node.name}"
        field_map[current_path] = node

        for child in node.children:
            field_map.update(self._build_field_map(child, current_path))

        return field_map

    def create_fragment(
        self, root_xpath: str, initial_data: Optional[Dict[str, Any]] = None
    ) -> HPXMLFragment:
        """Instantiate a fragment rooted at ``root_xpath``.

        Args:
            root_xpath: Absolute XPath serving as fragment root.
            initial_data: Optional mapping of xpath→value to pre-populate fields.

        Returns:
            Newly created fragment with fields discovered beneath the root.
        """
        fragment = HPXMLFragment(root_xpath=root_xpath)

        # Add all fields under the root xpath
        for xpath, rule_node in self.field_map.items():
            if xpath.startswith(root_xpath) and rule_node.kind == "field":
                initial_value = None
                if initial_data and xpath in initial_data:
                    initial_value = str(initial_data[xpath])

                field = HPXMLField(
                    xpath=xpath,
                    value=initial_value,
                    notes=rule_node.notes.copy() if rule_node.notes else [],
                )
                fragment.fields.append(field)

        return fragment

    def validate_fragment(self, fragment: HPXMLFragment) -> Dict[str, List[str]]:
        """Perform basic schema-level validation of fragment values.

        Checks required, enumeration membership, and primitive datatypes.
        Business/cross-field rules are not applied here.

        Returns:
            Mapping of xpath→list of error messages (empty if all fields valid).
        """
        validation_results = {}

        for field in fragment.fields:
            errors = []
            rule_node = self.field_map.get(field.xpath)

            if rule_node:
                # Check required fields
                if (
                    rule_node.min_occurs
                    and rule_node.min_occurs > 0
                    and not field.value
                ):
                    errors.append(f"Field '{rule_node.name}' is required")

                # Check enumerations
                if field.value and rule_node.enum_values:
                    if field.value not in rule_node.enum_values:
                        errors.append(
                            f"Value must be one of: {', '.join(rule_node.enum_values)}"
                        )

                # Check data type
                if field.value and rule_node.data_type:
                    if not self._validate_data_type(field.value, rule_node.data_type):
                        errors.append(f"Value must be a valid {rule_node.data_type}")

            field.validation_errors = errors
            if errors:
                validation_results[field.xpath] = errors

        return validation_results

    def _validate_data_type(self, value: str, data_type: str) -> bool:
        """Validate a value against a data type."""
        if data_type in ["integer", "positiveInteger"]:
            try:
                int_val = int(value)
                return int_val > 0 if data_type == "positiveInteger" else True
            except ValueError:
                return False
        elif data_type in ["decimal", "double"]:
            try:
                float(value)
                return True
            except ValueError:
                return False
        elif data_type == "boolean":
            return value.lower() in ["true", "false", "1", "0"]
        elif data_type == "date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
                return True
            except ValueError:
                return False
        return True

    def fragment_to_xml(self, fragment: HPXMLFragment) -> ET.Element:
        """Convert a fragment to an XML element tree (detached root)."""
        # Parse the root xpath to get element structure
        path_parts = fragment.root_xpath.strip("/").split("/")

        # Create root element
        root = ET.Element(path_parts[0])
        current = root

        # Build nested structure
        for part in path_parts[1:]:
            child = ET.SubElement(current, part)
            current = child

        # Add fields as child elements
        for field in fragment.fields:
            if field.value is not None:
                # Calculate relative path from root_xpath
                if field.xpath.startswith(fragment.root_xpath):
                    rel_path = field.xpath[len(fragment.root_xpath) :].strip("/")
                    if rel_path:
                        self._add_field_to_element(current, rel_path, field)

        return root

    def _add_field_to_element(
        self, parent: ET.Element, path: str, field: HPXMLField
    ) -> None:
        """Add a field to an XML element at the specified path."""
        if "/" in path:
            # Nested path - create intermediate elements
            parts = path.split("/", 1)
            child_name = parts[0]
            remaining_path = parts[1]

            # Find or create child element
            child = parent.find(child_name)
            if child is None:
                child = ET.SubElement(parent, child_name)

            self._add_field_to_element(child, remaining_path, field)
        else:
            # Leaf element - add field value
            element = ET.SubElement(parent, path)
            if field.value:
                element.text = field.value

            # Add attributes
            for attr_name, attr_value in field.attributes.items():
                element.set(attr_name, attr_value)

    def xml_to_fragment(
        self, xml_element: ET.Element, root_xpath: str
    ) -> HPXMLFragment:
        """Convert an XML subtree back into a fragment instance."""
        fragment = HPXMLFragment(root_xpath=root_xpath)

        # Extract fields from XML
        self._extract_fields_from_xml(xml_element, root_xpath, fragment.fields)

        return fragment

    def _extract_fields_from_xml(
        self, element: ET.Element, base_xpath: str, fields: List[HPXMLField]
    ) -> None:
        """Recursively extract fields from XML element."""
        for child in element:
            child_xpath = f"{base_xpath}/{child.tag}"

            # Check if this is a field or container
            rule_node = self.field_map.get(child_xpath)

            if rule_node and rule_node.kind == "field":
                # This is a field - extract value
                field = HPXMLField(
                    xpath=child_xpath, value=child.text, attributes=dict(child.attrib)
                )
                fields.append(field)
            else:
                # This is a container - recurse
                self._extract_fields_from_xml(child, child_xpath, fields)

    def fragment_to_dict(self, fragment: HPXMLFragment) -> Dict[str, Any]:
        """Serialize a fragment into an API-friendly dictionary structure."""
        return {
            "root_xpath": fragment.root_xpath,
            "schema_version": fragment.schema_version,
            "created_at": fragment.created_at,
            "modified_at": fragment.modified_at,
            "metadata": fragment.metadata,
            "fields": [
                {
                    "xpath": field.xpath,
                    "value": field.value,
                    "attributes": field.attributes,
                    "notes": field.notes,
                    "validation_errors": field.validation_errors,
                }
                for field in fragment.fields
            ],
        }

    def dict_to_fragment(self, data: Dict[str, Any]) -> HPXMLFragment:
        """Deserialize a fragment dictionary back into an object model."""
        fragment = HPXMLFragment(
            root_xpath=data["root_xpath"],
            schema_version=data.get("schema_version", "4.0"),
            created_at=data.get("created_at"),
            modified_at=data.get("modified_at"),
            metadata=data.get("metadata", {}),
        )

        for field_data in data.get("fields", []):
            field = HPXMLField(
                xpath=field_data["xpath"],
                value=field_data.get("value"),
                attributes=field_data.get("attributes", {}),
                notes=field_data.get("notes", []),
                validation_errors=field_data.get("validation_errors", []),
            )
            fragment.fields.append(field)

        return fragment

    def save_fragment(self, fragment: HPXMLFragment, file_path: Path) -> None:
        """Persist fragment as a formatted XML document to disk."""
        xml_root = self.fragment_to_xml(fragment)

        # Add metadata as comments
        tree = ET.ElementTree(xml_root)

        # Write to file with pretty formatting (Python <3.9 fallback)
        try:  # pragma: no cover - formatting nicety
            from xml.etree.ElementTree import indent as _et_indent  # type: ignore

            _et_indent(tree, space="  ", level=0)  # Python 3.9+
        except Exception:  # Fallback: manual naive indentation
            pass

        tree.write(file_path, encoding="utf-8", xml_declaration=True)

    def load_fragment(self, file_path: Path, root_xpath: str) -> HPXMLFragment:
        """Load XML from disk and extract a fragment rooted at ``root_xpath``."""
        tree = ET.parse(file_path)
        return self.xml_to_fragment(tree.getroot(), root_xpath)


class HPXMLFormBuilder:
    """Derive lightweight UI form schemas from the rule tree.

    Produces JSON-friendly objects including widget hints and basic validation
    metadata intended for dynamic form generation.
    """

    def __init__(self, rule_node: RuleNode):
        """Initialize with rule node."""
        self.rule_node = rule_node

    def build_form_schema(self, max_depth: int = 3) -> Dict[str, Any]:
        """Create a nested form schema up to a depth limit."""
        return self._build_node_schema(self.rule_node, max_depth, 0)

    def _build_node_schema(
        self, node: RuleNode, max_depth: int, current_depth: int
    ) -> Dict[str, Any]:
        """Internal recursive form schema builder for one node."""
        schema = {
            "name": node.name,
            "xpath": node.xpath,
            "kind": node.kind,
            "type": self._map_data_type(node.data_type),
            "required": bool(node.min_occurs and node.min_occurs > 0),
            "repeatable": node.repeatable,
            "description": node.description,
            "notes": node.notes.copy() if node.notes else [],
        }

        # Add validation rules
        if node.enum_values:
            schema["enum"] = node.enum_values
            schema["ui_widget"] = "select"

        if node.data_type == "boolean":
            schema["ui_widget"] = "checkbox"
        elif node.data_type in ["integer", "decimal"]:
            schema["ui_widget"] = "number"
        elif node.data_type == "date":
            schema["ui_widget"] = "date"

        # Add extension metadata
        if "extension_chain_truncated" in node.notes:
            schema["ui_hint"] = "This field has complex inheritance that was simplified"
        elif "extension_point" in node.notes:
            schema["ui_hint"] = "This allows custom data extensions"

        # Add children if within depth limit
        if current_depth < max_depth and node.children:
            children_list: List[Dict[str, Any]] = []
            for child in node.children:
                child_schema = self._build_node_schema(
                    child, max_depth, current_depth + 1
                )
                children_list.append(child_schema)
            schema["children"] = children_list

        return schema

    def _map_data_type(self, data_type: Optional[str]) -> str:
        """Translate XSD primitive type to generic form widget type."""
        if not data_type:
            return "string"

        type_map = {
            "integer": "integer",
            "positiveInteger": "integer",
            "decimal": "number",
            "double": "number",
            "boolean": "boolean",
            "date": "date",
            "dateTime": "datetime",
        }

        return type_map.get(data_type, "string")

    def get_field_dependencies(self, node: RuleNode) -> Dict[str, List[str]]:
        """Infer simple dependency relationships for conditional UI logic."""
        dependencies = {}

        # Simple dependency extraction based on notes and structure
        for child in node.children:
            child_deps = []

            # Check for choice groups
            if "choice" in child.notes:
                siblings = [
                    c.xpath for c in node.children if c != child and "choice" in c.notes
                ]
                if siblings:
                    child_deps.extend(siblings)

            # Check for extension dependencies
            if "extension_chain_truncated" in child.notes:
                # This field depends on understanding its base types
                child_deps.append("_base_type_selection")

            if child_deps:
                dependencies[child.xpath] = child_deps

            # Recurse for nested dependencies
            nested_deps = self.get_field_dependencies(child)
            dependencies.update(nested_deps)

        return dependencies
