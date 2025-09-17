"""Enhanced validation engine for HPXML documents.

This module layers business rule and contextual validation on top of the structural
schema (XSD) validation provided elsewhere in the package. It combines:

* Basic schema checks (datatype, enumeration membership, required vs optional)
* Schematron rule evaluation (assert/report style business constraints)
* Custom, pluggable rule functions (numeric ranges, conditional required fields, etc.)
* Cross-field and cross-section consistency checks (e.g., area and volume coherence)
* Bulk/document level aggregation with summarized error / warning statistics
* Lightweight performance instrumentation (latency + success/failure counts)

Design principles:
1. Non-fatal: A failure to evaluate an individual business rule should not abort the entire
   validation run—an error is downgraded to a warning where sensible to avoid hiding other issues.
2. Extensibility: Arbitrary custom rules can be registered through the context without editing
   the core engine, enabling downstream systems to inject domain logic.
3. Transparency: Each executed Schematron or custom rule can emit a structured record in
   ``ValidationResult.rule_results`` to aid debugging and UI presentation.
4. Performance Pragmatism: For interactive scenarios a simplified Schematron expression
   evaluator is used (pattern‑based) instead of a full XPath engine; the API surface is designed
   so a future swap with a proper evaluator is isolated to ``_evaluate_schematron_test``.

Typical usage patterns:

Single field validation::

    from hpxml_schema_api.enhanced_validation import get_enhanced_validator, ValidationContext

    validator = get_enhanced_validator()  # or pass a Path to a Schematron file
    context = ValidationContext(version="4.0")
    result = validator.validate_field("/HPXML/Building/BuildingDetails/Enclosure/Attic/Area", 1200, context)
    if not result.valid:
        print(result.errors)

Bulk (fragment) validation::

    field_values = {
        "/HPXML/Building/BuildingDetails/Enclosure/Attic/Area": 1200,
        "/HPXML/Building/BuildingDetails/Enclosure/Attic/Type": "vented"
    }
    bulk = validator.validate_bulk(field_values, context)
    print(bulk.summary)  # { 'total_errors': ..., 'fields_with_warnings': ... }

Whole document validation with cross‑field checks::

    doc_map = load_document_into_flat_path_map()  # user supplied helper
    bulk = validator.validate_document(doc_map)
    for field_result in bulk.results:
        ...

Custom rule injection (e.g., ensure attic area under threshold when vented)::

    custom_rule = {
        'type': 'numeric_range',  # will map to built‑in _validate_numeric_range
        'min': 0,
        'max': 5000
    }
    context.custom_rules.append(custom_rule)
    result = validator.validate_field(attic_area_xpath, 6000, context)
    assert any('above maximum' in e for e in result.errors)

Limitations:
* The Schematron evaluator is intentionally partial (string pattern heuristics only).
* No full XML DOM is constructed here; callers provide flattened path->value maps.
* Unit conversion logic is illustrative and not a substitute for a dedicated units library.

Future enhancements (documented for roadmap clarity):
* Replace simplified Schematron evaluation with lxml / Saxon based XPath execution.
* Add rule dependency graph to short‑circuit evaluations when prerequisites fail.
* Provide structured error codes (machine friendly) alongside human readable messages.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
from xml.etree import ElementTree as ET

from .models import RuleNode, ValidationRule
from .monitoring import get_monitor
from .schematron_parser import SchematronParser
from .version_manager import get_versioned_parser
from .xsd_parser import ParserConfig


@dataclass
class ValidationContext:
    """Context for validation operations.

    Attributes:
        version: HPXML schema version string used to acquire / cache the XSD parser.
        xpath_context: Optional base path for relative rule expressions (reserved for future use).
        parent_values: Pre-populated parent field values (can be leveraged by custom rules).
        document_data: Full (or partial) document flat map for cross-field validation.
        strict_mode: If True, non-fatal evaluator issues may be escalated to errors.
        custom_rules: List of dynamic rule definitions consumed by built-in validator hooks.

    Custom rule structure:
        A custom rule is a free-form dict whose "type" key matches a registered validator
        (see :meth:`BusinessRuleValidator._register_builtin_validators`). Additional keys are
        validator-specific, e.g. for a numeric range:

        ``{"type": "numeric_range", "min": 0, "max": 500}``

    Example:
        >>> ctx = ValidationContext(version="4.0")
        >>> ctx.custom_rules.append({"type": "numeric_range", "min": 1, "max": 10})
        >>> ctx.strict_mode
        False
    """

    version: str = "4.0"
    xpath_context: Optional[str] = None
    parent_values: Dict[str, Any] = field(default_factory=dict)
    document_data: Optional[Dict[str, Any]] = None
    strict_mode: bool = False
    custom_rules: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ValidationResult:
    """Result of a single field validation operation.

    Attributes:
        valid: True if no errors were recorded.
        field_path: XPath of the validated field.
        value: Original value under validation (unmodified).
        errors: List of error messages (failed schema or business rule assertions).
        warnings: Non-fatal warnings (soft constraints, suspicious patterns, evaluator issues).
        info: Informational notes (optional advisory messages from rules).
        rule_results: Structured dictionaries capturing raw rule evaluation outcomes.

    Example:
        >>> result = ValidationResult(True, "/HPXML/Some/Path", 42)
        >>> result.valid
        True
    """

    valid: bool
    field_path: str
    value: Any
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)
    rule_results: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class BulkValidationResult:
    """Aggregate result of multi-field (bulk or whole document) validation.

    Attributes:
        overall_valid: True if no individual field contained errors.
        total_fields: Number of fields processed.
        valid_fields: Count of fields with zero errors.
        invalid_fields: Count of fields with at least one error.
        results: Ordered list of individual :class:`ValidationResult` objects.
        summary: Derived counters (errors, warnings, info, fields with warnings, etc.).

    Example access pattern:
        >>> bulk = BulkValidationResult(True, 2, 2, 0, [], {"total_errors": 0})
        >>> bulk.summary.get("total_errors")
        0
    """

    overall_valid: bool
    total_fields: int
    valid_fields: int
    invalid_fields: int
    results: List[ValidationResult] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)


class BusinessRuleValidator:
    """Core business rule evaluator.

    Responsibilities:
        * Load & parse Schematron (if provided) to attach structured validation rules.
        * Perform base schema rule lookups through the versioned parser.
        * Execute custom rule functions registered internally or supplied via context.
        * Produce granular rule evaluation artifacts for traceability.

    The class is intentionally kept independent of transport / framework concerns so it can
    be reused inside API endpoints, CLI commands, or batch processing jobs.

    Example (standalone usage)::

        brv = BusinessRuleValidator(Path("rules/my_business_rules.sch"))
        ctx = ValidationContext(version="4.0")
        single = brv.validate_field("/HPXML/.../Area", 900, ctx)
        bulk = brv.validate_bulk({"/HPXML/.../Area": 900, "/HPXML/.../Type": "vented"}, ctx)
    """

    def __init__(self, schematron_path: Optional[Path] = None):
        """Initialize business rule validator.

        Args:
            schematron_path: Path to Schematron file with business rules
        """
        self.schematron_path = schematron_path
        self.schematron_parser: Optional[SchematronParser] = None
        self.custom_validators: Dict[str, Callable] = {}

        if schematron_path and schematron_path.exists():
            self.schematron_parser = SchematronParser(schematron_path)

        # Register built-in validators
        self._register_builtin_validators()

    def _register_builtin_validators(self) -> None:
        """Register built-in validation functions."""
        self.custom_validators.update(
            {
                "numeric_range": self._validate_numeric_range,
                "date_format": self._validate_date_format,
                "conditional_required": self._validate_conditional_required,
                "cross_field_consistency": self._validate_cross_field_consistency,
                "enumeration_subset": self._validate_enumeration_subset,
                "unit_conversion": self._validate_unit_conversion,
            }
        )

    def validate_field(
        self, field_path: str, value: Any, context: ValidationContext
    ) -> ValidationResult:
        """Validate a single field.

        This performs (in order):
            1. Schema lookup (and existence check) via versioned parser
            2. Basic XSD constraints (datatype, enumeration, required rule)
            3. Schematron rule evaluation (if a parser is present)
            4. Custom rule execution (context supplied + built-ins)
            5. Cross-field rules (when document_data present)

        Args:
            field_path: Absolute XPath to validate.
            value: Raw value (string / numeric / other) to examine.
            context: Active :class:`ValidationContext` providing version, custom rules and document map.

        Returns:
            ValidationResult with errors, warnings, info and rule outcome details.

        Example:
            >>> ctx = ValidationContext(version="4.0")
            >>> ctx.custom_rules.append({"type": "numeric_range", "min": 0, "max": 100})
            >>> v = BusinessRuleValidator()
            >>> res = v.validate_field("/HPXML/Building/.../SomeNumericField", 150, ctx)
            >>> res.valid
            False
        """
        result = ValidationResult(valid=True, field_path=field_path, value=value)

        # Get schema information for the field
        parser = get_versioned_parser(context.version)
        if not parser:
            result.errors.append(f"Schema version {context.version} not available")
            result.valid = False
            return result

        try:
            schema_tree = parser.parse_xsd()
            field_node = self._find_field_node(schema_tree, field_path)

            if field_node is None:
                result.errors.append(f"Field {field_path} not found in schema")
                result.valid = False
                return result

            # Basic schema validation
            self._validate_basic_schema(field_node, value, result)

            # Schematron business rules
            if self.schematron_parser:
                self._validate_schematron_rules(field_node, value, context, result)

            # Custom business rules
            self._validate_custom_rules(field_node, value, context, result)

            # Cross-field validation if context provided
            if context.document_data:
                self._validate_cross_field_rules(field_node, value, context, result)

        except Exception as e:
            result.errors.append(f"Validation error: {str(e)}")
            result.valid = False

        # Determine overall validity
        result.valid = len(result.errors) == 0

        return result

    def validate_bulk(
        self, field_values: Dict[str, Any], context: ValidationContext
    ) -> BulkValidationResult:
        """Validate many fields (fragment or whole-document subset) in one pass.

        The context's ``document_data`` is populated to enable cross-field business rules.
        Individual field errors do not halt processing; all results are collected.

        Args:
            field_values: Mapping of XPath -> value pairs to validate.
            context: Validation context (mutated to include ``document_data`` reference).

        Returns:
            BulkValidationResult with per-field results and summary counters.

        Example:
            >>> ctx = ValidationContext(version="4.0")
            >>> values = {"/HPXML/.../FieldA": 5, "/HPXML/.../FieldB": 10}
            >>> validator = BusinessRuleValidator()
            >>> bulk = validator.validate_bulk(values, ctx)
            >>> bulk.total_fields
            2
        """
        # Update context with all field values for cross-field validation
        context.document_data = field_values

        results = []
        for field_path, value in field_values.items():
            field_result = self.validate_field(field_path, value, context)
            results.append(field_result)

        # Calculate summary statistics
        total_fields = len(results)
        valid_fields = sum(1 for r in results if r.valid)
        invalid_fields = total_fields - valid_fields

        summary = {
            "total_errors": sum(len(r.errors) for r in results),
            "total_warnings": sum(len(r.warnings) for r in results),
            "total_info": sum(len(r.info) for r in results),
            "fields_with_errors": sum(1 for r in results if r.errors),
            "fields_with_warnings": sum(1 for r in results if r.warnings),
        }

        return BulkValidationResult(
            overall_valid=invalid_fields == 0,
            total_fields=total_fields,
            valid_fields=valid_fields,
            invalid_fields=invalid_fields,
            results=results,
            summary=summary,
        )

    def _find_field_node(
        self, schema_tree: RuleNode, field_path: str
    ) -> Optional[RuleNode]:
        """Find a field node in the schema tree by XPath."""

        def search_node(node: RuleNode, target_path: str) -> Optional[RuleNode]:
            if node.xpath == target_path:
                return node
            for child in node.children:
                found = search_node(child, target_path)
                if found:
                    return found
            return None

        return search_node(schema_tree, field_path)

    def _validate_basic_schema(
        self, field_node: RuleNode, value: Any, result: ValidationResult
    ) -> None:
        """Validate against basic schema constraints."""
        # Data type validation
        if field_node.data_type and value is not None:
            if not self._validate_data_type(value, field_node.data_type):
                result.errors.append(
                    f"Value '{value}' is not valid for type {field_node.data_type}"
                )

        # Enumeration validation
        if field_node.enum_values and value is not None:
            if str(value) not in field_node.enum_values:
                result.errors.append(
                    f"Value '{value}' not in allowed values: {field_node.enum_values}"
                )

        # Required field validation
        if field_node.min_occurs and field_node.min_occurs > 0 and value is None:
            result.errors.append(
                f"Field {field_node.xpath} is required but no value provided"
            )

    def _validate_data_type(self, value: Any, data_type: str) -> bool:
        """Validate value against XSD data type."""
        if value is None:
            return True

        value_str = str(value)

        try:
            if data_type in ["int", "integer", "positiveInteger", "nonNegativeInteger"]:
                int(value_str)
                if data_type == "positiveInteger" and int(value_str) <= 0:
                    return False
                if data_type == "nonNegativeInteger" and int(value_str) < 0:
                    return False
            elif data_type in ["float", "double", "decimal"]:
                float(value_str)
            elif data_type == "boolean":
                return value_str.lower() in ["true", "false", "1", "0"]
            elif data_type == "date":
                # Basic date format check (YYYY-MM-DD)
                return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", value_str))
            elif data_type == "dateTime":
                # Basic datetime format check
                return bool(
                    re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value_str)
                )
            # string and other types are generally valid
            return True
        except (ValueError, AttributeError):
            return False

    def _validate_schematron_rules(
        self,
        field_node: RuleNode,
        value: Any,
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate against Schematron business rules."""
        if not self.schematron_parser:
            return

        for validation_rule in field_node.validations:
            if not validation_rule.test:
                continue

            try:
                # Simple test evaluation - in a real implementation,
                # this would use an XPath evaluator with the actual document
                rule_passed = self._evaluate_schematron_test(
                    validation_rule.test, value, context
                )

                rule_result = {
                    "rule": validation_rule.test,
                    "message": validation_rule.message,
                    "severity": validation_rule.severity,
                    "passed": rule_passed,
                }

                result.rule_results.append(rule_result)

                if not rule_passed:
                    if validation_rule.severity.lower() in ["error", "fatal"]:
                        result.errors.append(validation_rule.message)
                    elif validation_rule.severity.lower() == "warning":
                        result.warnings.append(validation_rule.message)
                    else:
                        result.info.append(validation_rule.message)

            except Exception as e:
                result.warnings.append(
                    f"Error evaluating rule '{validation_rule.test}': {str(e)}"
                )

    def _evaluate_schematron_test(
        self, test: str, value: Any, context: ValidationContext
    ) -> bool:
        """Evaluate a Schematron test expression.

        This is a simplified evaluator. A full implementation would use
        an XPath engine with the complete XML document.
        """
        try:
            # Simple pattern matching for common test patterns
            # Check for negation first
            if "not(" in test:
                # Negation tests - find matching closing parenthesis
                start_idx = test.find("not(") + 4
                paren_count = 1
                end_idx = start_idx

                for i, char in enumerate(test[start_idx:], start_idx):
                    if char == "(":
                        paren_count += 1
                    elif char == ")":
                        paren_count -= 1
                        if paren_count == 0:
                            end_idx = i
                            break

                inner_test = test[start_idx:end_idx]
                return not self._evaluate_schematron_test(inner_test, value, context)

            elif "string-length(" in test and value is not None:
                # Extract length requirement – pattern simplified
                gt_match = re.search(r">\s*(\d+)", test)
                gte_match = re.search(r">=\s*(\d+)", test)
                if gt_match and not gte_match:
                    min_length = int(gt_match.group(1))
                    return len(str(value)) > min_length
                if gte_match:
                    min_length = int(gte_match.group(1))
                    return len(str(value)) >= min_length

            elif "number(" in test and value is not None:
                # Numeric range tests (simple heuristic parsing)
                try:
                    num_value = float(value)
                    # Order of checks matters to avoid '>=' being caught by '>' etc.
                    gte_match = re.search(r">=\s*([\d.]+)", test)
                    lte_match = re.search(r"<=\s*([\d.]+)", test)
                    gt_match = (
                        re.search(r">\s*([\d.]+)", test) if not gte_match else None
                    )
                    lt_match = (
                        re.search(r"<\s*([\d.]+)", test) if not lte_match else None
                    )
                    if gte_match:
                        min_val = float(gte_match.group(1))
                        return num_value >= min_val
                    if gt_match:
                        min_val = float(gt_match.group(1))
                        return num_value > min_val
                    if lte_match:
                        max_val = float(lte_match.group(1))
                        return num_value <= max_val
                    if lt_match:
                        max_val = float(lt_match.group(1))
                        return num_value < max_val
                except (ValueError, AttributeError):
                    return False

            # Default to true for unrecognized patterns
            return True

        except Exception:
            # If evaluation fails, assume the test passes to avoid false positives
            return True

    def _validate_custom_rules(
        self,
        field_node: RuleNode,
        value: Any,
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate against custom business rules."""
        for custom_rule in context.custom_rules:
            rule_type = custom_rule.get("type")
            if rule_type in self.custom_validators:
                validator = self.custom_validators[rule_type]
                try:
                    validator(field_node, value, custom_rule, context, result)
                except Exception as e:
                    result.warnings.append(
                        f"Error in custom rule {rule_type}: {str(e)}"
                    )

    def _validate_cross_field_rules(
        self,
        field_node: RuleNode,
        value: Any,
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate cross-field dependencies and consistency."""
        if not context.document_data:
            return

        # Example: Heating/Cooling system consistency
        if "HeatingSystem" in field_node.xpath and value:
            cooling_system = context.document_data.get(
                "/HPXML/Building/BuildingDetails/Systems/HVAC/HVACPlant/CoolingSystem"
            )
            if cooling_system and value == cooling_system:
                result.warnings.append(
                    "Heating and cooling systems should typically be different"
                )

        # Example: Area consistency checks
        if "ConditionedFloorArea" in field_node.xpath and value:
            try:
                conditioned_area = float(value)
                total_area = context.document_data.get(
                    "/HPXML/Building/BuildingDetails/BuildingSummary/BuildingConstruction/ConditionedBuildingVolume"
                )
                if (
                    total_area and float(total_area) / 8 > conditioned_area * 1.5
                ):  # Assuming 8ft ceiling
                    result.warnings.append(
                        "Conditioned volume seems inconsistent with floor area"
                    )
            except (ValueError, TypeError):
                pass

    # Built-in validator implementations
    def _validate_numeric_range(
        self,
        field_node: RuleNode,
        value: Any,
        rule: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate numeric value is within specified range."""
        try:
            num_value = float(value)
            min_val = rule.get("min")
            max_val = rule.get("max")

            if min_val is not None and num_value < min_val:
                result.errors.append(f"Value {num_value} is below minimum {min_val}")
            if max_val is not None and num_value > max_val:
                result.errors.append(f"Value {num_value} is above maximum {max_val}")
        except (ValueError, TypeError):
            result.errors.append(f"Value '{value}' is not numeric")

    def _validate_date_format(
        self,
        field_node: RuleNode,
        value: Any,
        rule: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate date format."""
        if value is None:
            return

        date_format = rule.get("format", "%Y-%m-%d")
        try:
            from datetime import datetime

            datetime.strptime(str(value), date_format)
        except ValueError:
            result.errors.append(f"Date '{value}' does not match format {date_format}")

    def _validate_conditional_required(
        self,
        field_node: RuleNode,
        value: Any,
        rule: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate conditional requirements based on other field values."""
        condition_field = rule.get("condition_field")
        condition_value = rule.get("condition_value")

        if not condition_field or not context.document_data:
            return

        actual_condition_value = context.document_data.get(condition_field)
        if actual_condition_value == condition_value and not value:
            result.errors.append(
                f"Field is required when {condition_field} = {condition_value}"
            )

    def _validate_cross_field_consistency(
        self,
        field_node: RuleNode,
        value: Any,
        rule: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate consistency between related fields."""
        related_field = rule.get("related_field")
        consistency_rule = rule.get("rule", "equal")

        if not related_field or not context.document_data:
            return

        related_value = context.document_data.get(related_field)
        if related_value is None:
            return

        try:
            if consistency_rule == "equal" and value != related_value:
                result.warnings.append(
                    f"Inconsistent with related field {related_field}"
                )
            elif consistency_rule == "greater_than" and float(value) <= float(
                related_value
            ):
                result.warnings.append(f"Should be greater than {related_field} value")
            elif consistency_rule == "less_than" and float(value) >= float(
                related_value
            ):
                result.warnings.append(f"Should be less than {related_field} value")
        except (ValueError, TypeError):
            pass

    def _validate_enumeration_subset(
        self,
        field_node: RuleNode,
        value: Any,
        rule: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate that value is in a contextual subset of allowed values."""
        subset_values = rule.get("subset", [])
        subset_condition = rule.get("condition")

        if subset_condition and context.document_data:
            condition_field = subset_condition.get("field")
            condition_value = subset_condition.get("value")

            if condition_field in context.document_data:
                actual_value = context.document_data[condition_field]
                if actual_value == condition_value and value not in subset_values:
                    result.errors.append(
                        f"Value '{value}' not allowed in this context. Allowed: {subset_values}"
                    )

    def _validate_unit_conversion(
        self,
        field_node: RuleNode,
        value: Any,
        rule: Dict[str, Any],
        context: ValidationContext,
        result: ValidationResult,
    ) -> None:
        """Validate units and perform conversions if needed."""
        expected_unit = rule.get("expected_unit")
        value_unit = rule.get("value_unit")

        if not expected_unit or not value_unit:
            return

        # Simple unit validation (would need a proper unit conversion library)
        compatible_units = {
            "ft": ["feet", "foot"],
            "m": ["meter", "metres"],
            "sqft": ["ft2", "square_feet"],
            "sqm": ["m2", "square_meters"],
        }

        if expected_unit in compatible_units:
            if (
                value_unit not in compatible_units[expected_unit]
                and value_unit != expected_unit
            ):
                result.warnings.append(
                    f"Unit '{value_unit}' may not be compatible with expected '{expected_unit}'"
                )


class EnhancedValidationEngine:
    """Facade that wraps ``BusinessRuleValidator`` and records metrics.

    This higher-level API mirrors ``BusinessRuleValidator`` methods while adding timing and
    success/failure status recording via :mod:`hpxml_schema_api.monitoring`.

    Example:
        >>> engine = EnhancedValidationEngine()
        >>> field_res = engine.validate_field("/HPXML/.../Value", 123)
        >>> bulk_res = engine.validate_bulk({"/HPXML/.../Value": 123})
        >>> doc_res = engine.validate_document({"/HPXML/.../Value": 123, ".../Other": 456})
    """

    def __init__(self, schematron_path: Optional[Path] = None):
        """Initialize enhanced validation engine.

        Args:
            schematron_path: Path to Schematron business rules file
        """
        self.business_rule_validator = BusinessRuleValidator(schematron_path)

    def validate_field(
        self, field_path: str, value: Any, context: Optional[ValidationContext] = None
    ) -> ValidationResult:
        """Validate a single field and record timing metrics.

        Args:
            field_path: XPath of the field under test.
            value: Candidate value to validate.
            context: Optional pre-configured :class:`ValidationContext`.

        Returns:
            ValidationResult with detailed validation information.
        """
        start_time = time.time()

        if context is None:
            context = ValidationContext()

        try:
            result = self.business_rule_validator.validate_field(
                field_path, value, context
            )

            # Record metrics
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"enhanced_validation_field",
                time.time() - start_time,
                200 if result.valid else 400,
            )

            return result

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"enhanced_validation_field", time.time() - start_time, 500
            )
            return ValidationResult(
                valid=False,
                field_path=field_path,
                value=value,
                errors=[f"Validation engine error: {str(e)}"],
            )

    def validate_bulk(
        self, field_values: Dict[str, Any], context: Optional[ValidationContext] = None
    ) -> BulkValidationResult:
        """Validate multiple fields and emit aggregated performance metrics.

        Args:
            field_values: Mapping of XPath -> value pairs.
            context: Optional validation context.

        Returns:
            BulkValidationResult with per-field outcomes and summary counts.
        """
        start_time = time.time()

        if context is None:
            context = ValidationContext()

        try:
            result = self.business_rule_validator.validate_bulk(field_values, context)

            # Record metrics
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"enhanced_validation_bulk",
                time.time() - start_time,
                200 if result.overall_valid else 400,
            )

            return result

        except Exception as e:
            monitor = get_monitor()
            monitor.record_endpoint_request(
                f"enhanced_validation_bulk", time.time() - start_time, 500
            )
            return BulkValidationResult(
                overall_valid=False,
                total_fields=len(field_values),
                valid_fields=0,
                invalid_fields=len(field_values),
                results=[
                    ValidationResult(
                        valid=False,
                        field_path=path,
                        value=value,
                        errors=[f"Validation engine error: {str(e)}"],
                    )
                    for path, value in field_values.items()
                ],
            )

    def validate_document(
        self, document_data: Dict[str, Any], context: Optional[ValidationContext] = None
    ) -> BulkValidationResult:
        """Validate an entire HPXML document represented as a flattened map.

        The helper simply points ``context.document_data`` at the supplied mapping then delegates
        to :meth:`validate_bulk` so all cross-field validators are active.

        Args:
            document_data: Complete document flat mapping ``{xpath: value}``.
            context: Optional validation context instance.

        Returns:
            BulkValidationResult with comprehensive validation results.
        """
        if context is None:
            context = ValidationContext()

        # Set document data for cross-field validation
        context.document_data = document_data

        return self.validate_bulk(document_data, context)


# Global enhanced validation engine instance
_enhanced_validator: Optional[EnhancedValidationEngine] = None


def get_enhanced_validator(
    schematron_path: Optional[Path] = None,
) -> EnhancedValidationEngine:
    """Return a process-wide singleton ``EnhancedValidationEngine``.

    A thin convenience wrapper useful for frameworks that prefer implicit dependency access.
    The first call optionally seeds the engine with a Schematron file path; subsequent calls
    ignore any differing argument and return the originally constructed instance.

    Example:
        >>> engine1 = get_enhanced_validator()
        >>> engine2 = get_enhanced_validator()
        >>> assert engine1 is engine2
    """
    global _enhanced_validator
    if _enhanced_validator is None:
        _enhanced_validator = EnhancedValidationEngine(schematron_path)
    return _enhanced_validator
