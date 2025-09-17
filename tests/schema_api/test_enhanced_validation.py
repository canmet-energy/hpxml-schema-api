"""Tests for enhanced validation functionality."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from hpxml_schema_api.enhanced_validation import (
    ValidationContext,
    ValidationResult,
    BulkValidationResult,
    BusinessRuleValidator,
    EnhancedValidationEngine,
    get_enhanced_validator
)
from hpxml_schema_api.models import RuleNode, ValidationRule


class TestValidationContext:
    """Test ValidationContext dataclass."""

    def test_create_validation_context(self):
        """Test creating validation context with all parameters."""
        context = ValidationContext(
            version="4.1",
            xpath_context="/HPXML/Building",
            parent_values={"BuildingID": "test-123"},
            document_data={"/HPXML/Building/BuildingID": "test-123"},
            strict_mode=True,
            custom_rules=[{"type": "numeric_range", "min": 0, "max": 100}]
        )

        assert context.version == "4.1"
        assert context.xpath_context == "/HPXML/Building"
        assert context.parent_values == {"BuildingID": "test-123"}
        assert context.document_data == {"/HPXML/Building/BuildingID": "test-123"}
        assert context.strict_mode is True
        assert len(context.custom_rules) == 1

    def test_default_values(self):
        """Test default values for ValidationContext."""
        context = ValidationContext()

        assert context.version == "4.0"
        assert context.xpath_context is None
        assert context.parent_values == {}
        assert context.document_data is None
        assert context.strict_mode is False
        assert context.custom_rules == []


class TestValidationResult:
    """Test ValidationResult dataclass."""

    def test_create_validation_result(self):
        """Test creating validation result."""
        result = ValidationResult(
            valid=False,
            field_path="/HPXML/Building/BuildingID",
            value="test-123",
            errors=["Error 1", "Error 2"],
            warnings=["Warning 1"],
            info=["Info 1"],
            rule_results=[{"rule": "test", "passed": False}]
        )

        assert result.valid is False
        assert result.field_path == "/HPXML/Building/BuildingID"
        assert result.value == "test-123"
        assert len(result.errors) == 2
        assert len(result.warnings) == 1
        assert len(result.info) == 1
        assert len(result.rule_results) == 1

    def test_default_empty_lists(self):
        """Test that lists default to empty."""
        result = ValidationResult(
            valid=True,
            field_path="/test",
            value="test"
        )

        assert result.errors == []
        assert result.warnings == []
        assert result.info == []
        assert result.rule_results == []


class TestBulkValidationResult:
    """Test BulkValidationResult dataclass."""

    def test_create_bulk_validation_result(self):
        """Test creating bulk validation result."""
        individual_results = [
            ValidationResult(valid=True, field_path="/field1", value="value1"),
            ValidationResult(valid=False, field_path="/field2", value="value2", errors=["Error"])
        ]

        result = BulkValidationResult(
            overall_valid=False,
            total_fields=2,
            valid_fields=1,
            invalid_fields=1,
            results=individual_results,
            summary={"total_errors": 1, "total_warnings": 0}
        )

        assert result.overall_valid is False
        assert result.total_fields == 2
        assert result.valid_fields == 1
        assert result.invalid_fields == 1
        assert len(result.results) == 2
        assert result.summary["total_errors"] == 1


class TestBusinessRuleValidator:
    """Test BusinessRuleValidator class."""

    def setup_method(self):
        """Set up test environment."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.validator = BusinessRuleValidator()

    def teardown_method(self):
        """Clean up test environment."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_init_without_schematron(self):
        """Test initialization without Schematron file."""
        validator = BusinessRuleValidator()
        assert validator.schematron_path is None
        assert validator.schematron_parser is None
        assert len(validator.custom_validators) > 0  # Built-in validators registered

    def test_init_with_nonexistent_schematron(self):
        """Test initialization with non-existent Schematron file."""
        nonexistent_path = self.temp_dir / "nonexistent.sch"
        validator = BusinessRuleValidator(nonexistent_path)
        assert validator.schematron_path == nonexistent_path
        assert validator.schematron_parser is None

    def test_init_with_valid_schematron(self):
        """Test initialization with valid Schematron file."""
        schematron_content = '''<?xml version="1.0" encoding="UTF-8"?>
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron">
    <sch:pattern>
        <sch:rule context="/HPXML/Building">
            <sch:assert test="BuildingID">Building must have an ID</sch:assert>
        </sch:rule>
    </sch:pattern>
</sch:schema>'''

        schematron_file = self.temp_dir / "test.sch"
        schematron_file.write_text(schematron_content)

        validator = BusinessRuleValidator(schematron_file)
        assert validator.schematron_path == schematron_file
        assert validator.schematron_parser is not None

    @patch('hpxml_schema_api.enhanced_validation.get_versioned_parser')
    def test_validate_field_success(self, mock_get_parser):
        """Test successful field validation."""
        # Mock parser and schema tree
        mock_parser = MagicMock()
        mock_node = RuleNode(
            xpath="/HPXML/Building/BuildingID",
            name="BuildingID",
            kind="field",
            data_type="string",
            min_occurs=1
        )
        mock_parser.parse_xsd.return_value = MagicMock()
        mock_get_parser.return_value = mock_parser

        # Mock _find_field_node to return our test node
        with patch.object(self.validator, '_find_field_node', return_value=mock_node):
            context = ValidationContext(version="4.0")
            result = self.validator.validate_field(
                "/HPXML/Building/BuildingID",
                "test-123",
                context
            )

        assert isinstance(result, ValidationResult)
        assert result.field_path == "/HPXML/Building/BuildingID"
        assert result.value == "test-123"

    @patch('hpxml_schema_api.enhanced_validation.get_versioned_parser')
    def test_validate_field_parser_unavailable(self, mock_get_parser):
        """Test validation when parser is unavailable."""
        mock_get_parser.return_value = None

        context = ValidationContext(version="99.0")
        result = self.validator.validate_field("/test/field", "value", context)

        assert result.valid is False
        assert "Schema version 99.0 not available" in result.errors

    @patch('hpxml_schema_api.enhanced_validation.get_versioned_parser')
    def test_validate_field_not_found(self, mock_get_parser):
        """Test validation when field is not found in schema."""
        mock_parser = MagicMock()
        mock_parser.parse_xsd.return_value = MagicMock()
        mock_get_parser.return_value = mock_parser

        # Mock _find_field_node to return None
        with patch.object(self.validator, '_find_field_node', return_value=None):
            context = ValidationContext(version="4.0")
            result = self.validator.validate_field("/nonexistent/field", "value", context)

        assert result.valid is False
        assert "Field /nonexistent/field not found in schema" in result.errors

    def test_validate_bulk(self):
        """Test bulk validation."""
        field_values = {
            "/HPXML/Building/BuildingID": "test-123",
            "/HPXML/Building/Area": "1500"
        }

        # Mock validate_field to return different results
        with patch.object(self.validator, 'validate_field') as mock_validate:
            mock_validate.side_effect = [
                ValidationResult(valid=True, field_path="/HPXML/Building/BuildingID", value="test-123"),
                ValidationResult(valid=False, field_path="/HPXML/Building/Area", value="1500", errors=["Invalid area"])
            ]

            context = ValidationContext(version="4.0")
            result = self.validator.validate_bulk(field_values, context)

        assert isinstance(result, BulkValidationResult)
        assert result.total_fields == 2
        assert result.valid_fields == 1
        assert result.invalid_fields == 1
        assert result.overall_valid is False
        assert len(result.results) == 2
        assert result.summary["total_errors"] == 1

    def test_validate_data_type_integer(self):
        """Test data type validation for integers."""
        assert self.validator._validate_data_type("123", "int") is True
        assert self.validator._validate_data_type("123", "integer") is True
        assert self.validator._validate_data_type("abc", "int") is False
        assert self.validator._validate_data_type("12.5", "int") is False

    def test_validate_data_type_positive_integer(self):
        """Test data type validation for positive integers."""
        assert self.validator._validate_data_type("123", "positiveInteger") is True
        assert self.validator._validate_data_type("0", "positiveInteger") is False
        assert self.validator._validate_data_type("-1", "positiveInteger") is False

    def test_validate_data_type_float(self):
        """Test data type validation for floats."""
        assert self.validator._validate_data_type("123.45", "float") is True
        assert self.validator._validate_data_type("123", "float") is True
        assert self.validator._validate_data_type("abc", "float") is False

    def test_validate_data_type_boolean(self):
        """Test data type validation for booleans."""
        assert self.validator._validate_data_type("true", "boolean") is True
        assert self.validator._validate_data_type("false", "boolean") is True
        assert self.validator._validate_data_type("1", "boolean") is True
        assert self.validator._validate_data_type("0", "boolean") is True
        assert self.validator._validate_data_type("yes", "boolean") is False

    def test_validate_data_type_date(self):
        """Test data type validation for dates."""
        assert self.validator._validate_data_type("2024-01-15", "date") is True
        assert self.validator._validate_data_type("invalid-date", "date") is False

    def test_validate_data_type_none_value(self):
        """Test data type validation with None value."""
        assert self.validator._validate_data_type(None, "int") is True
        assert self.validator._validate_data_type(None, "string") is True

    def test_evaluate_schematron_test_string_length(self):
        """Test Schematron test evaluation for string length."""
        context = ValidationContext()

        # Test string length greater than
        assert self.validator._evaluate_schematron_test(
            "string-length(.) > 5", "test-123", context
        ) is True
        assert self.validator._evaluate_schematron_test(
            "string-length(.) > 10", "test", context
        ) is False

        # Test string length greater than or equal
        assert self.validator._evaluate_schematron_test(
            "string-length(.) >= 8", "test-123", context
        ) is True

    def test_evaluate_schematron_test_numeric(self):
        """Test Schematron test evaluation for numeric values."""
        context = ValidationContext()

        # Test numeric greater than
        assert self.validator._evaluate_schematron_test(
            "number(.) > 100", "150", context
        ) is True
        assert self.validator._evaluate_schematron_test(
            "number(.) > 100", "50", context
        ) is False

        # Test numeric less than
        assert self.validator._evaluate_schematron_test(
            "number(.) < 100", "50", context
        ) is True

    def test_evaluate_schematron_test_negation(self):
        """Test Schematron test evaluation for negation."""
        context = ValidationContext()

        # Test basic string length first
        result1 = self.validator._evaluate_schematron_test("string-length(.) > 10", "test", context)
        assert result1 is False  # "test" length is 4, not > 10

        result2 = self.validator._evaluate_schematron_test("string-length(.) > 2", "test", context)
        assert result2 is True  # "test" length is 4, which is > 2

        # Test negation
        assert self.validator._evaluate_schematron_test(
            "not(string-length(.) > 10)", "test", context
        ) is True  # not(False) = True
        assert self.validator._evaluate_schematron_test(
            "not(string-length(.) > 2)", "test", context
        ) is False  # not(True) = False

    def test_builtin_validator_numeric_range(self):
        """Test built-in numeric range validator."""
        field_node = RuleNode(xpath="/test", name="test", kind="field")
        rule = {"min": 0, "max": 100}
        context = ValidationContext()
        result = ValidationResult(valid=True, field_path="/test", value=50)

        self.validator._validate_numeric_range(field_node, "50", rule, context, result)
        assert result.valid is True
        assert len(result.errors) == 0

        # Test below minimum
        result = ValidationResult(valid=True, field_path="/test", value=-10)
        self.validator._validate_numeric_range(field_node, "-10", rule, context, result)
        assert len(result.errors) == 1
        assert "below minimum" in result.errors[0]

        # Test above maximum
        result = ValidationResult(valid=True, field_path="/test", value=150)
        self.validator._validate_numeric_range(field_node, "150", rule, context, result)
        assert len(result.errors) == 1
        assert "above maximum" in result.errors[0]

    def test_builtin_validator_date_format(self):
        """Test built-in date format validator."""
        field_node = RuleNode(xpath="/test", name="test", kind="field")
        rule = {"format": "%Y-%m-%d"}
        context = ValidationContext()

        # Valid date
        result = ValidationResult(valid=True, field_path="/test", value="2024-01-15")
        self.validator._validate_date_format(field_node, "2024-01-15", rule, context, result)
        assert len(result.errors) == 0

        # Invalid date
        result = ValidationResult(valid=True, field_path="/test", value="invalid-date")
        self.validator._validate_date_format(field_node, "invalid-date", rule, context, result)
        assert len(result.errors) == 1
        assert "does not match format" in result.errors[0]

    def test_builtin_validator_conditional_required(self):
        """Test built-in conditional required validator."""
        field_node = RuleNode(xpath="/test", name="test", kind="field")
        rule = {
            "condition_field": "/HPXML/Building/Type",
            "condition_value": "SingleFamily"
        }
        context = ValidationContext(
            document_data={"/HPXML/Building/Type": "SingleFamily"}
        )

        # Required field missing
        result = ValidationResult(valid=True, field_path="/test", value=None)
        self.validator._validate_conditional_required(field_node, None, rule, context, result)
        assert len(result.errors) == 1
        assert "required when" in result.errors[0]

        # Required field present
        result = ValidationResult(valid=True, field_path="/test", value="value")
        self.validator._validate_conditional_required(field_node, "value", rule, context, result)
        assert len(result.errors) == 0

    def test_builtin_validator_cross_field_consistency(self):
        """Test built-in cross-field consistency validator."""
        field_node = RuleNode(xpath="/test", name="test", kind="field")
        rule = {
            "related_field": "/HPXML/Building/MaxArea",
            "rule": "less_than"
        }
        context = ValidationContext(
            document_data={"/HPXML/Building/MaxArea": "2000"}
        )

        # Value is less than related field (valid)
        result = ValidationResult(valid=True, field_path="/test", value="1500")
        self.validator._validate_cross_field_consistency(field_node, "1500", rule, context, result)
        assert len(result.warnings) == 0

        # Value is greater than related field (invalid)
        result = ValidationResult(valid=True, field_path="/test", value="2500")
        self.validator._validate_cross_field_consistency(field_node, "2500", rule, context, result)
        assert len(result.warnings) == 1
        assert "Should be less than" in result.warnings[0]


class TestEnhancedValidationEngine:
    """Test EnhancedValidationEngine class."""

    def setup_method(self):
        """Set up test environment."""
        self.engine = EnhancedValidationEngine()

    @patch('hpxml_schema_api.enhanced_validation.get_monitor')
    def test_validate_field_success(self, mock_get_monitor):
        """Test successful field validation through engine."""
        mock_monitor = MagicMock()
        mock_get_monitor.return_value = mock_monitor

        with patch.object(self.engine.business_rule_validator, 'validate_field') as mock_validate:
            mock_result = ValidationResult(
                valid=True,
                field_path="/test/field",
                value="test-value"
            )
            mock_validate.return_value = mock_result

            result = self.engine.validate_field("/test/field", "test-value")

        assert result is mock_result
        mock_monitor.record_endpoint_request.assert_called_once()

    @patch('hpxml_schema_api.enhanced_validation.get_monitor')
    def test_validate_field_error(self, mock_get_monitor):
        """Test field validation error handling."""
        mock_monitor = MagicMock()
        mock_get_monitor.return_value = mock_monitor

        with patch.object(self.engine.business_rule_validator, 'validate_field') as mock_validate:
            mock_validate.side_effect = Exception("Validation error")

            result = self.engine.validate_field("/test/field", "test-value")

        assert result.valid is False
        assert "Validation engine error" in result.errors[0]
        mock_monitor.record_endpoint_request.assert_called_with(
            "enhanced_validation_field", pytest.approx(0, abs=1), 500
        )

    @patch('hpxml_schema_api.enhanced_validation.get_monitor')
    def test_validate_bulk_success(self, mock_get_monitor):
        """Test successful bulk validation through engine."""
        mock_monitor = MagicMock()
        mock_get_monitor.return_value = mock_monitor

        field_values = {"/field1": "value1", "/field2": "value2"}

        with patch.object(self.engine.business_rule_validator, 'validate_bulk') as mock_validate:
            mock_result = BulkValidationResult(
                overall_valid=True,
                total_fields=2,
                valid_fields=2,
                invalid_fields=0
            )
            mock_validate.return_value = mock_result

            result = self.engine.validate_bulk(field_values)

        assert result is mock_result
        mock_monitor.record_endpoint_request.assert_called_once()

    def test_validate_document(self):
        """Test document validation."""
        document_data = {
            "/HPXML/Building/BuildingID": "test-123",
            "/HPXML/Building/Area": "1500"
        }

        with patch.object(self.engine, 'validate_bulk') as mock_validate_bulk:
            mock_result = BulkValidationResult(
                overall_valid=True,
                total_fields=2,
                valid_fields=2,
                invalid_fields=0
            )
            mock_validate_bulk.return_value = mock_result

            result = self.engine.validate_document(document_data)

        assert result is mock_result
        # Verify context was set with document data
        call_args = mock_validate_bulk.call_args
        context = call_args[0][1]  # Second argument is context
        assert context.document_data == document_data


class TestGlobalEnhancedValidator:
    """Test global enhanced validator functions."""

    def test_get_enhanced_validator_singleton(self):
        """Test that get_enhanced_validator returns singleton."""
        validator1 = get_enhanced_validator()
        validator2 = get_enhanced_validator()
        assert validator1 is validator2

    def test_get_enhanced_validator_with_schematron(self):
        """Test get_enhanced_validator with Schematron path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            schematron_path = Path(temp_dir) / "test.sch"
            schematron_path.write_text("<?xml version='1.0'?><schema/>")

            # Clear global instance to test with schematron path
            import hpxml_schema_api.enhanced_validation
            hpxml_schema_api.enhanced_validation._enhanced_validator = None

            validator = get_enhanced_validator(schematron_path)
            assert isinstance(validator, EnhancedValidationEngine)


class TestIntegrationValidation:
    """Integration tests for enhanced validation."""

    def test_complete_validation_workflow(self):
        """Test complete validation workflow with mock data."""
        # Create test field node
        test_node = RuleNode(
            xpath="/HPXML/Building/BuildingID",
            name="BuildingID",
            kind="field",
            data_type="string",
            min_occurs=1,
            enum_values=["residential", "commercial"],
            validations=[
                ValidationRule(
                    message="BuildingID must be at least 5 characters",
                    severity="error",
                    test="string-length(.) >= 5",
                    context="/HPXML/Building"
                )
            ]
        )

        validator = BusinessRuleValidator()

        # Mock parser and schema tree
        with patch('hpxml_schema_api.enhanced_validation.get_versioned_parser') as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_xsd.return_value = MagicMock()
            mock_get_parser.return_value = mock_parser

            with patch.object(validator, '_find_field_node', return_value=test_node):
                context = ValidationContext(version="4.0")

                # Test valid value
                result = validator.validate_field(
                    "/HPXML/Building/BuildingID",
                    "residential",
                    context
                )

                assert result.valid is True
                assert result.field_path == "/HPXML/Building/BuildingID"
                assert result.value == "residential"

                # Test invalid enumeration
                result = validator.validate_field(
                    "/HPXML/Building/BuildingID",
                    "invalid_type",
                    context
                )

                assert result.valid is False
                assert any("not in allowed values" in error for error in result.errors)

    def test_custom_rules_integration(self):
        """Test integration with custom validation rules."""
        validator = BusinessRuleValidator()

        test_node = RuleNode(
            xpath="/HPXML/Building/Area",
            name="Area",
            kind="field",
            data_type="float"
        )

        custom_rules = [
            {
                "type": "numeric_range",
                "min": 500,
                "max": 10000
            }
        ]

        context = ValidationContext(
            version="4.0",
            custom_rules=custom_rules
        )

        with patch('hpxml_schema_api.enhanced_validation.get_versioned_parser') as mock_get_parser:
            mock_parser = MagicMock()
            mock_parser.parse_xsd.return_value = MagicMock()
            mock_get_parser.return_value = mock_parser

            with patch.object(validator, '_find_field_node', return_value=test_node):
                # Test valid value
                result = validator.validate_field("/HPXML/Building/Area", "1500", context)
                assert result.valid is True

                # Test invalid value (too small)
                result = validator.validate_field("/HPXML/Building/Area", "100", context)
                assert result.valid is False
                assert any("below minimum" in error for error in result.errors)

                # Test invalid value (too large)
                result = validator.validate_field("/HPXML/Building/Area", "15000", context)
                assert result.valid is False
                assert any("above maximum" in error for error in result.errors)