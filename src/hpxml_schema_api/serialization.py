"""HPXML serialization utilities for round-trip form editing."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

from .models import RuleNode


@dataclass
class HPXMLField:
    """Represents a field value in an HPXML document."""
    xpath: str
    value: Optional[str] = None
    attributes: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)


@dataclass
class HPXMLFragment:
    """Represents a portion of an HPXML document with form data."""
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
    """Serialize and deserialize HPXML fragments for form editing."""

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

    def create_fragment(self, root_xpath: str, initial_data: Optional[Dict[str, Any]] = None) -> HPXMLFragment:
        """Create a new HPXML fragment with optional initial data."""
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
                    notes=rule_node.notes.copy() if rule_node.notes else []
                )
                fragment.fields.append(field)

        return fragment

    def validate_fragment(self, fragment: HPXMLFragment) -> Dict[str, List[str]]:
        """Validate all fields in a fragment against schema rules."""
        validation_results = {}

        for field in fragment.fields:
            errors = []
            rule_node = self.field_map.get(field.xpath)

            if rule_node:
                # Check required fields
                if rule_node.min_occurs and rule_node.min_occurs > 0 and not field.value:
                    errors.append(f"Field '{rule_node.name}' is required")

                # Check enumerations
                if field.value and rule_node.enum_values:
                    if field.value not in rule_node.enum_values:
                        errors.append(f"Value must be one of: {', '.join(rule_node.enum_values)}")

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
        """Convert a fragment to XML format."""
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
                    rel_path = field.xpath[len(fragment.root_xpath):].strip("/")
                    if rel_path:
                        self._add_field_to_element(current, rel_path, field)

        return root

    def _add_field_to_element(self, parent: ET.Element, path: str, field: HPXMLField) -> None:
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

    def xml_to_fragment(self, xml_element: ET.Element, root_xpath: str) -> HPXMLFragment:
        """Convert XML element to HPXML fragment."""
        fragment = HPXMLFragment(root_xpath=root_xpath)

        # Extract fields from XML
        self._extract_fields_from_xml(xml_element, root_xpath, fragment.fields)

        return fragment

    def _extract_fields_from_xml(self, element: ET.Element, base_xpath: str, fields: List[HPXMLField]) -> None:
        """Recursively extract fields from XML element."""
        for child in element:
            child_xpath = f"{base_xpath}/{child.tag}"

            # Check if this is a field or container
            rule_node = self.field_map.get(child_xpath)

            if rule_node and rule_node.kind == "field":
                # This is a field - extract value
                field = HPXMLField(
                    xpath=child_xpath,
                    value=child.text,
                    attributes=dict(child.attrib)
                )
                fields.append(field)
            else:
                # This is a container - recurse
                self._extract_fields_from_xml(child, child_xpath, fields)

    def fragment_to_dict(self, fragment: HPXMLFragment) -> Dict[str, Any]:
        """Convert fragment to dictionary format for API responses."""
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
            ]
        }

    def dict_to_fragment(self, data: Dict[str, Any]) -> HPXMLFragment:
        """Convert dictionary to HPXML fragment."""
        fragment = HPXMLFragment(
            root_xpath=data["root_xpath"],
            schema_version=data.get("schema_version", "4.0"),
            created_at=data.get("created_at"),
            modified_at=data.get("modified_at"),
            metadata=data.get("metadata", {})
        )

        for field_data in data.get("fields", []):
            field = HPXMLField(
                xpath=field_data["xpath"],
                value=field_data.get("value"),
                attributes=field_data.get("attributes", {}),
                notes=field_data.get("notes", []),
                validation_errors=field_data.get("validation_errors", [])
            )
            fragment.fields.append(field)

        return fragment

    def save_fragment(self, fragment: HPXMLFragment, file_path: Path) -> None:
        """Save fragment to XML file."""
        xml_root = self.fragment_to_xml(fragment)

        # Add metadata as comments
        tree = ET.ElementTree(xml_root)

        # Write to file with pretty formatting
        ET.indent(tree, space="  ", level=0)
        tree.write(file_path, encoding="utf-8", xml_declaration=True)

    def load_fragment(self, file_path: Path, root_xpath: str) -> HPXMLFragment:
        """Load fragment from XML file."""
        tree = ET.parse(file_path)
        return self.xml_to_fragment(tree.getroot(), root_xpath)


class HPXMLFormBuilder:
    """Build form configurations from HPXML rule nodes."""

    def __init__(self, rule_node: RuleNode):
        """Initialize with rule node."""
        self.rule_node = rule_node

    def build_form_schema(self, max_depth: int = 3) -> Dict[str, Any]:
        """Build a JSON schema for form generation."""
        return self._build_node_schema(self.rule_node, max_depth, 0)

    def _build_node_schema(self, node: RuleNode, max_depth: int, current_depth: int) -> Dict[str, Any]:
        """Build schema for a single node."""
        schema = {
            "name": node.name,
            "xpath": node.xpath,
            "kind": node.kind,
            "type": self._map_data_type(node.data_type),
            "required": bool(node.min_occurs and node.min_occurs > 0),
            "repeatable": node.repeatable,
            "description": node.description,
            "notes": node.notes.copy() if node.notes else []
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
            schema["children"] = []
            for child in node.children:
                child_schema = self._build_node_schema(child, max_depth, current_depth + 1)
                schema["children"].append(child_schema)

        return schema

    def _map_data_type(self, data_type: Optional[str]) -> str:
        """Map XSD data types to form types."""
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
        """Extract field dependencies for conditional form logic."""
        dependencies = {}

        # Simple dependency extraction based on notes and structure
        for child in node.children:
            child_deps = []

            # Check for choice groups
            if "choice" in child.notes:
                siblings = [c.xpath for c in node.children if c != child and "choice" in c.notes]
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