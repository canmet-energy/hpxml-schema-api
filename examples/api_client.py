#!/usr/bin/env python3
"""
Example client for the HPXML Rules API.

This script demonstrates how to interact with the HPXML Rules API
for schema exploration, validation, dynamic form generation, and
handling extension metadata and serialization features.
"""

import json
from typing import Dict, List, Optional, Any
import httpx
from pathlib import Path


class HPXMLRulesClient:
    """Client for interacting with the HPXML Rules API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        """Initialize the client with the API base URL."""
        self.base_url = base_url
        self.client = httpx.Client(base_url=base_url, timeout=30.0)
        self.etag_cache: Dict[str, str] = {}

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close the client."""
        self.client.close()

    def get_health(self) -> Dict:
        """Check API health status."""
        response = self.client.get("/health")
        response.raise_for_status()
        return response.json()

    def get_metadata(self, use_cache: bool = True) -> Optional[Dict]:
        """
        Get schema metadata with caching support.

        Args:
            use_cache: Whether to use cached ETag

        Returns:
            Metadata dict or None if not modified (304)
        """
        headers = {}
        if use_cache and "metadata" in self.etag_cache:
            headers["If-None-Match"] = self.etag_cache["metadata"]

        response = self.client.get("/metadata", headers=headers)

        if response.status_code == 304:
            return None  # Not modified, use cached version

        response.raise_for_status()

        # Store ETag for future requests
        if "ETag" in response.headers:
            self.etag_cache["metadata"] = response.headers["ETag"]

        return response.json()

    def get_tree(
        self,
        section: Optional[str] = None,
        depth: Optional[int] = None
    ) -> Dict:
        """
        Get the schema tree structure.

        Args:
            section: Optional HPXML xpath to start from
            depth: Maximum depth to traverse (1-10)

        Returns:
            Tree node structure
        """
        params = {}
        if section:
            params["section"] = section
        if depth:
            params["depth"] = depth

        response = self.client.get("/tree", params=params)
        response.raise_for_status()
        return response.json()

    def get_fields(self, section: str) -> Dict:
        """
        Get field information for a specific section.

        Args:
            section: HPXML xpath of the section

        Returns:
            Section info with fields and children
        """
        response = self.client.get("/fields", params={"section": section})
        response.raise_for_status()
        return response.json()

    def search(
        self,
        query: str,
        kind: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        Search for nodes by name or xpath.

        Args:
            query: Search term (min 2 characters)
            kind: Optional filter by node kind (field, section, choice)
            limit: Maximum results (1-500)

        Returns:
            List of matching nodes
        """
        params = {"query": query, "limit": limit}
        if kind:
            params["kind"] = kind

        response = self.client.get("/search", params=params)
        response.raise_for_status()
        return response.json()["results"]

    def validate(
        self,
        xpath: str,
        value: Optional[str] = None,
        context: Optional[Dict[str, str]] = None
    ) -> Dict:
        """
        Validate a value against schema rules.

        Args:
            xpath: HPXML xpath to validate
            value: Optional value to validate
            context: Optional context for validation

        Returns:
            Validation result with errors and warnings
        """
        payload = {"xpath": xpath}
        if value is not None:
            payload["value"] = value
        if context:
            payload["context"] = context

        response = self.client.post("/validate", json=payload)
        response.raise_for_status()
        return response.json()

    def validate_bulk(
        self,
        validations: List[Dict[str, Any]]
    ) -> Dict:
        """
        Validate multiple values in a single request.

        Args:
            validations: List of validation requests

        Returns:
            Bulk validation results
        """
        payload = {"validations": validations}
        response = self.client.post("/validate/bulk", json=payload)
        response.raise_for_status()
        return response.json()

    def get_parser_config(self) -> Dict:
        """
        Get current parser configuration.

        Returns:
            Parser configuration settings
        """
        response = self.client.get("/config/parser")
        response.raise_for_status()
        return response.json()

    def update_parser_config(self, config: Dict[str, Any]) -> Dict:
        """
        Update parser configuration.

        Args:
            config: New configuration settings

        Returns:
            Updated configuration
        """
        response = self.client.post("/config/parser", json=config)
        response.raise_for_status()
        return response.json()

    def _format_extension_metadata(self, node: Dict[str, Any]) -> str:
        """
        Format extension metadata for display.

        Args:
            node: Node with potential extension metadata

        Returns:
            Formatted extension info string
        """
        notes = node.get("notes", [])
        extension_info = ""

        if "extension_chain_truncated" in notes:
            # Find inheritance depth info
            inheritance_note = next(
                (note for note in notes if note.startswith("inherits_from_")),
                None
            )
            if inheritance_note:
                depth = inheritance_note.split("_")[2]
                extension_info += f" ⚠️ (truncated {depth}-level inheritance)"
            else:
                extension_info += " ⚠️ (truncated inheritance)"

        elif "extension_point" in notes:
            extension_info += " 🔌 (extension point)"

        return extension_info

    def explore_section(self, xpath: str, indent: int = 0, show_extensions: bool = True) -> None:
        """
        Recursively explore and print a section's structure with extension metadata.

        Args:
            xpath: Section xpath to explore
            indent: Current indentation level
            show_extensions: Whether to show extension metadata
        """
        fields_data = self.get_fields(xpath)
        section = fields_data["section"]

        # Print section info with extension metadata
        prefix = "  " * indent
        section_name = f"{section['name']} ({section['kind']})"

        if show_extensions:
            extension_info = self._format_extension_metadata(section)
            section_name += extension_info

        print(f"{prefix}{section_name}")

        # Show truncation explanation if available
        if show_extensions and "extension_chain_truncated" in section.get("notes", []):
            if section.get("description"):
                print(f"{prefix}  💡 {section['description']}")

        # Print fields with extension metadata
        for field in fields_data["fields"]:
            field_info = f"{prefix}  - {field['name']}"
            if field.get("data_type"):
                field_info += f" ({field['data_type']})"
            if field.get("min_occurs", 0) > 0:
                field_info += " [required]"
            if field.get("enumerations"):
                field_info += f" choices: {', '.join(field['enumerations'][:3])}"
                if len(field["enumerations"]) > 3:
                    field_info += "..."

            # Add extension metadata for fields
            if show_extensions:
                extension_info = self._format_extension_metadata(field)
                field_info += extension_info

            print(field_info)

        # Explore child sections (limit depth to avoid too much output)
        if indent < 2:
            for child in fields_data["children"]:
                self.explore_section(child["xpath"], indent + 1, show_extensions)

    def demonstrate_form_generation(self, xpath: str) -> Dict[str, Any]:
        """
        Demonstrate form schema generation with extension handling.

        Args:
            xpath: Section to generate form for

        Returns:
            Form schema with extension metadata
        """
        fields_data = self.get_fields(xpath)

        # Build form schema
        form_schema = {
            "title": fields_data["section"]["name"],
            "type": "object",
            "properties": {},
            "required": [],
            "ui_schema": {}
        }

        # Add extension warning if needed
        section = fields_data["section"]
        if "extension_chain_truncated" in section.get("notes", []):
            form_schema["ui_schema"]["ui:description"] = (
                "⚠️ This form may be incomplete due to complex inheritance. "
                "Some fields from base types may not be shown."
            )

        # Process fields
        for field in fields_data["fields"]:
            field_name = field["name"]
            field_schema = {
                "type": self._map_data_type(field.get("data_type", "string")),
                "title": field_name
            }

            # Add validation rules
            if field.get("enumerations"):
                field_schema["enum"] = field["enumerations"]
                form_schema["ui_schema"][field_name] = {"ui:widget": "select"}

            # Mark required fields
            if field.get("min_occurs", 0) > 0:
                form_schema["required"].append(field_name)

            # Add extension hints
            if "extension_point" in field.get("notes", []):
                field_schema["description"] = "🔌 This field allows custom extensions"

            form_schema["properties"][field_name] = field_schema

        return form_schema

    def _map_data_type(self, xsd_type: str) -> str:
        """Map XSD data types to JSON Schema types."""
        type_map = {
            "integer": "integer",
            "positiveInteger": "integer",
            "decimal": "number",
            "double": "number",
            "boolean": "boolean",
            "date": "string",
            "dateTime": "string"
        }
        return type_map.get(xsd_type, "string")


def main():
    """Demonstrate API client usage."""

    print("HPXML Rules API Client Example")
    print("=" * 50)

    with HPXMLRulesClient() as client:
        # 1. Check health
        print("\n1. Checking API health...")
        health = client.get_health()
        print(f"   Status: {health['status']}")
        print(f"   Schema Version: {health.get('schema_version', 'unknown')}")

        # 2. Get metadata
        print("\n2. Getting schema metadata...")
        metadata = client.get_metadata()
        if metadata:
            print(f"   Schema: {metadata.get('schema_version')}")
            print(f"   Source: {metadata.get('source')}")
            print(f"   Generated: {metadata.get('generated_at')}")
        else:
            print("   Using cached metadata (not modified)")

        # 3. Search for wall-related fields
        print("\n3. Searching for 'wall' fields...")
        wall_fields = client.search("wall", kind="field", limit=5)
        for field in wall_fields:
            print(f"   - {field['name']}: {field['xpath']}")

        # 4. Validate a year value
        print("\n4. Validating year built value...")
        validation = client.validate(
            "/HPXML/Building/BuildingDetails/BuildingSummary/YearBuilt",
            "2024"
        )
        print(f"   Valid: {validation['valid']}")
        if validation['errors']:
            print(f"   Errors: {', '.join(validation['errors'])}")
        if validation['warnings']:
            print(f"   Warnings: {', '.join(validation['warnings'])}")

        # 5. Explore a section structure
        print("\n5. Exploring Walls section structure...")
        try:
            client.explore_section(
                "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                print("   Wall section not found in schema")
            else:
                raise

        # 6. Get tree with depth limit
        print("\n6. Getting tree structure (depth=2)...")
        tree = client.get_tree(depth=2)
        root = tree["node"]
        print(f"   Root: {root['name']}")
        if root.get("children"):
            for child in root["children"][:3]:  # Show first 3 children
                print(f"   └─ {child['name']} ({child['kind']})")
                if child.get("children"):
                    for grandchild in child["children"][:2]:  # Show first 2 grandchildren
                        print(f"      └─ {grandchild['name']} ({grandchild['kind']})")

        # 7. Test parser configuration
        print("\n7. Testing parser configuration...")
        try:
            config = client.get_parser_config()
            print(f"   Max extension depth: {config.get('max_extension_depth')}")
            print(f"   Max recursion depth: {config.get('max_recursion_depth')}")
            print(f"   Extension metadata: {config.get('track_extension_metadata')}")
        except httpx.HTTPStatusError:
            print("   Parser configuration endpoint not available")

        # 8. Demonstrate bulk validation
        print("\n8. Testing bulk validation...")
        bulk_validations = [
            {"xpath": "/HPXML/Building/BuildingDetails/BuildingSummary/YearBuilt", "value": "2024"},
            {"xpath": "/HPXML/Building/BuildingDetails/BuildingSummary/YearBuilt", "value": "1800"},
            {"xpath": "/HPXML/Building/BuildingDetails/BuildingSummary/YearBuilt", "value": "invalid"}
        ]
        try:
            bulk_results = client.validate_bulk(bulk_validations)
            for i, result in enumerate(bulk_results["results"]):
                value = bulk_validations[i]["value"]
                status = "✓" if result["valid"] else "✗"
                print(f"   {status} Year {value}: {'Valid' if result['valid'] else 'Invalid'}")
        except httpx.HTTPStatusError:
            print("   Bulk validation endpoint not available")

        # 9. Demonstrate form generation with extension handling
        print("\n9. Form generation with extension metadata...")
        try:
            form_schema = client.demonstrate_form_generation(
                "/HPXML/Building/BuildingDetails/Enclosure/Walls/Wall"
            )
            print(f"   Form title: {form_schema['title']}")
            print(f"   Fields: {len(form_schema['properties'])}")
            if "ui:description" in form_schema["ui_schema"]:
                print(f"   Extension warning: {form_schema['ui_schema']['ui:description']}")
        except httpx.HTTPStatusError:
            print("   Could not generate form schema for Wall section")

        # 10. Demonstrate extension metadata visualization
        print("\n10. Extension metadata in tree exploration...")
        print("    Exploring with extension indicators:")
        try:
            # Search for sections with extension metadata
            extension_sections = client.search("extension", kind="section", limit=3)
            for section in extension_sections:
                print(f"    Found: {section['name']} at {section['xpath']}")
                if section.get("notes"):
                    notes = [note for note in section["notes"] if "extension" in note]
                    if notes:
                        print(f"      Extension notes: {', '.join(notes)}")
        except httpx.HTTPStatusError:
            print("    No extension sections found")

        # 11. Demonstrate caching
        print("\n11. Testing caching with metadata...")
        metadata1 = client.get_metadata()
        print("    First request: Data received")

        metadata2 = client.get_metadata(use_cache=True)
        if metadata2 is None:
            print("    Second request: Using cache (304 Not Modified)")
        else:
            print("    Second request: Data received (cache miss)")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print("\nError: Could not connect to API server.")
        print("Make sure the server is running: python -m h2k_hpxml.schema_api.run_server")
    except Exception as e:
        print(f"\nError: {e}")