"""Comprehensive tests for GraphQL API functionality."""

import json
import time
from typing import Dict, Any
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from hpxml_schema_api.app import app
from hpxml_schema_api.models import RuleNode, ValidationRule
from hpxml_schema_api.graphql_schema import schema, RuleNode as GraphQLRuleNode, ValidationRule as GraphQLValidationRule


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def sample_rule_node():
    """Create a sample RuleNode for testing."""
    return RuleNode(
        name="TestNode",
        xpath="/test/node",
        kind="field",
        data_type="string",
        description="Test node for GraphQL testing",
        min_occurs=1,
        max_occurs="1",
        repeatable=False,
        enum_values=["value1", "value2"],
        notes=["Test note"],
        validations=[
            ValidationRule(
                message="Test validation",
                severity="error",
                test="test != ''",
                context="test context"
            )
        ]
    )


@pytest.fixture
def nested_rule_node():
    """Create a nested RuleNode for testing depth limiting."""
    child_node = RuleNode(
        name="ChildNode",
        xpath="/test/node/child",
        kind="field",
        data_type="integer"
    )

    grandchild_node = RuleNode(
        name="GrandchildNode",
        xpath="/test/node/child/grandchild",
        kind="field",
        data_type="boolean"
    )

    child_node.children = [grandchild_node]

    parent_node = RuleNode(
        name="ParentNode",
        xpath="/test/node",
        kind="section",
        children=[child_node]
    )

    return parent_node


class TestGraphQLEndpoint:
    """Test GraphQL endpoint accessibility and basic functionality."""

    def test_graphql_endpoint_exists(self, client):
        """Test that GraphQL endpoint is accessible."""
        response = client.post("/graphql", json={
            "query": "{ health }"
        })
        assert response.status_code == 200

    def test_graphiql_interface_accessible(self, client):
        """Test that GraphiQL interface is accessible."""
        response = client.get("/graphql")
        assert response.status_code == 200
        # Should contain GraphiQL interface HTML
        assert "GraphiQL" in response.text or "graphql" in response.text.lower()

    def test_invalid_graphql_query(self, client):
        """Test handling of invalid GraphQL queries."""
        response = client.post("/graphql", json={
            "query": "{ invalidField }"
        })
        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
        assert len(data["errors"]) > 0

    def test_malformed_graphql_request(self, client):
        """Test handling of malformed GraphQL requests."""
        response = client.post("/graphql", json={
            "invalid": "request"
        })
        assert response.status_code in [400, 422]  # Bad request or validation error


class TestGraphQLQueries:
    """Test GraphQL query functionality."""

    def test_health_query(self, client):
        """Test health check query."""
        query = "{ health }"
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert data["data"]["health"] == "OK"

    def test_metadata_query(self, client):
        """Test metadata query."""
        query = """
        {
            metadata {
                version
                rootName
                totalNodes
                totalFields
                totalSections
                lastUpdated
                etag
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "metadata" in data["data"]

        metadata = data["data"]["metadata"]
        assert "version" in metadata
        assert "rootName" in metadata
        assert "etag" in metadata

    def test_tree_query_basic(self, client):
        """Test basic tree query."""
        query = """
        {
            tree {
                xpath
                name
                kind
                dataType
                description
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        # tree might be null if no schema is loaded

    def test_tree_query_with_depth(self, client):
        """Test tree query with depth parameter."""
        query = """
        {
            tree(depth: 2) {
                xpath
                name
                children {
                    xpath
                    name
                    children {
                        xpath
                        name
                    }
                }
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_fields_query(self, client):
        """Test fields query."""
        query = """
        {
            fields(limit: 10) {
                xpath
                name
                kind
                dataType
                description
                enumValues
                repeatable
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "fields" in data["data"]

    def test_search_query(self, client):
        """Test search query."""
        query = """
        {
            search(query: "test", limit: 5) {
                xpath
                name
                kind
                dataType
                description
                notes
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "search" in data["data"]

    def test_search_query_with_filters(self, client):
        """Test search query with kind filter."""
        query = """
        {
            search(query: "test", kind: FIELD, limit: 5, offset: 0) {
                xpath
                name
                kind
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data

    def test_search_query_minimum_length(self, client):
        """Test search query with minimum query length."""
        query = """
        {
            search(query: "a") {
                xpath
                name
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert data["data"]["search"] == []  # Should return empty for short queries

    def test_performance_metrics_query(self, client):
        """Test performance metrics query."""
        query = """
        {
            performanceMetrics {
                totalRequests
                averageResponseTime
                fastestResponseTime
                slowestResponseTime
                errorRate
                endpoints
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "performanceMetrics" in data["data"]

        metrics = data["data"]["performanceMetrics"]
        assert "totalRequests" in metrics
        assert "averageResponseTime" in metrics
        assert "endpoints" in metrics

    def test_cache_metrics_query(self, client):
        """Test cache metrics query."""
        query = """
        {
            cacheMetrics {
                cacheHits
                cacheMisses
                hitRate
                cacheSize
                memoryUsageMb
                evictions
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "cacheMetrics" in data["data"]

        metrics = data["data"]["cacheMetrics"]
        assert "cacheHits" in metrics
        assert "cacheMisses" in metrics
        assert "hitRate" in metrics


class TestGraphQLMutations:
    """Test GraphQL mutation functionality."""

    def test_validate_field_mutation(self, client):
        """Test single field validation mutation."""
        mutation = """
        mutation {
            validateField(input: {
                xpath: "/HPXML/Building/BuildingDetails/YearBuilt"
                value: "2024"
            }) {
                valid
                errors
                warnings
            }
        }
        """
        response = client.post("/graphql", json={"query": mutation})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "validateField" in data["data"]

        result = data["data"]["validateField"]
        assert "valid" in result
        assert "errors" in result
        assert "warnings" in result

    def test_validate_bulk_mutation(self, client):
        """Test bulk validation mutation."""
        mutation = """
        mutation {
            validateBulk(inputs: [
                {
                    xpath: "/HPXML/Building/BuildingDetails/YearBuilt"
                    value: "2024"
                },
                {
                    xpath: "/HPXML/Building/BuildingDetails/NumberofUnits"
                    value: "1"
                }
            ]) {
                valid
                errors
                warnings
            }
        }
        """
        response = client.post("/graphql", json={"query": mutation})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "validateBulk" in data["data"]

        results = data["data"]["validateBulk"]
        assert len(results) == 2
        for result in results:
            assert "valid" in result
            assert "errors" in result
            assert "warnings" in result

    def test_reset_metrics_mutation(self, client):
        """Test reset metrics mutation."""
        mutation = """
        mutation {
            resetMetrics
        }
        """
        response = client.post("/graphql", json={"query": mutation})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert data["data"]["resetMetrics"] is True


class TestGraphQLDepthLimiting:
    """Test GraphQL query depth limiting functionality."""

    def test_query_depth_limit_enforcement(self, client):
        """Test that deeply nested queries are rejected."""
        # Create a query that exceeds the depth limit (10)
        nested_query = "{ tree { " + "children { " * 15 + "name" + " }" * 15 + " } }"

        response = client.post("/graphql", json={"query": nested_query})

        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
        # Should contain depth limit error
        error_messages = [error.get("message", "") for error in data["errors"]]
        assert any("depth" in msg.lower() for msg in error_messages)

    def test_query_within_depth_limit(self, client):
        """Test that queries within depth limit are allowed."""
        # Create a query within the depth limit
        nested_query = """
        {
            tree {
                name
                children {
                    name
                    children {
                        name
                        children {
                            name
                        }
                    }
                }
            }
        }
        """

        response = client.post("/graphql", json={"query": nested_query})

        assert response.status_code == 200
        data = response.json()
        # Should not have depth limit errors
        if "errors" in data:
            error_messages = [error.get("message", "") for error in data["errors"]]
            assert not any("depth" in msg.lower() for msg in error_messages)


class TestGraphQLComplexQueries:
    """Test complex GraphQL queries and combinations."""

    def test_combined_query_multiple_fields(self, client):
        """Test query combining multiple root fields."""
        query = """
        {
            health
            metadata {
                version
                rootName
            }
            performanceMetrics {
                totalRequests
                averageResponseTime
            }
        }
        """
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "health" in data["data"]
        assert "metadata" in data["data"]
        assert "performanceMetrics" in data["data"]

    def test_query_with_variables(self, client):
        """Test GraphQL query with variables."""
        query = """
        query GetSearchResults($searchQuery: String!, $limit: Int) {
            search(query: $searchQuery, limit: $limit) {
                xpath
                name
                kind
            }
        }
        """
        variables = {
            "searchQuery": "building",
            "limit": 5
        }

        response = client.post("/graphql", json={
            "query": query,
            "variables": variables
        })

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "search" in data["data"]

    def test_mutation_with_variables(self, client):
        """Test GraphQL mutation with variables."""
        mutation = """
        mutation ValidateWithVariables($input: ValidationInput!) {
            validateField(input: $input) {
                valid
                errors
                warnings
            }
        }
        """
        variables = {
            "input": {
                "xpath": "/HPXML/Building/BuildingDetails/YearBuilt",
                "value": "2024"
            }
        }

        response = client.post("/graphql", json={
            "query": mutation,
            "variables": variables
        })

        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "validateField" in data["data"]


class TestGraphQLTypeConversions:
    """Test GraphQL type conversion functionality."""

    def test_validation_rule_conversion(self):
        """Test ValidationRule model to GraphQL type conversion."""
        rule = ValidationRule(
            message="Test message",
            severity="error",
            test="test != ''",
            context="test context"
        )

        graphql_rule = GraphQLValidationRule.from_model(rule)

        assert graphql_rule.message == "Test message"
        assert graphql_rule.severity == "error"
        assert graphql_rule.test == "test != ''"
        assert graphql_rule.context == "test context"

    def test_rule_node_conversion(self, sample_rule_node):
        """Test RuleNode model to GraphQL type conversion."""
        graphql_node = GraphQLRuleNode.from_model(sample_rule_node)

        assert graphql_node.name == "TestNode"
        assert graphql_node.xpath == "/test/node"
        assert graphql_node.kind == "field"
        assert graphql_node.data_type == "string"
        assert graphql_node.description == "Test node for GraphQL testing"
        assert graphql_node.min_occurs == 1
        assert graphql_node.max_occurs == "1"
        assert graphql_node.repeatable is False
        assert graphql_node.enum_values == ["value1", "value2"]
        assert graphql_node.notes == ["Test note"]
        assert len(graphql_node.validations) == 1

    def test_rule_node_conversion_with_depth_limit(self, nested_rule_node):
        """Test RuleNode conversion with depth limiting."""
        # Convert with depth limit of 1
        graphql_node = GraphQLRuleNode.from_model(nested_rule_node, max_depth=1)

        assert graphql_node.name == "ParentNode"
        assert len(graphql_node.children) == 1
        assert graphql_node.children[0].name == "ChildNode"
        # Grandchild should not be included due to depth limit
        assert len(graphql_node.children[0].children) == 0

    def test_rule_node_conversion_no_depth_limit(self, nested_rule_node):
        """Test RuleNode conversion without depth limiting."""
        graphql_node = GraphQLRuleNode.from_model(nested_rule_node)

        assert graphql_node.name == "ParentNode"
        assert len(graphql_node.children) == 1
        assert graphql_node.children[0].name == "ChildNode"
        # Grandchild should be included without depth limit
        assert len(graphql_node.children[0].children) == 1
        assert graphql_node.children[0].children[0].name == "GrandchildNode"


class TestGraphQLPerformance:
    """Test GraphQL performance characteristics."""

    def test_query_response_time(self, client):
        """Test that GraphQL queries respond within acceptable time."""
        query = "{ health }"

        start_time = time.time()
        response = client.post("/graphql", json={"query": query})
        response_time = time.time() - start_time

        assert response.status_code == 200
        assert response_time < 0.1  # Should respond within 100ms

    def test_complex_query_performance(self, client):
        """Test performance of complex nested queries."""
        query = """
        {
            metadata {
                version
                rootName
                totalNodes
                totalFields
            }
            performanceMetrics {
                totalRequests
                averageResponseTime
                endpoints
            }
            cacheMetrics {
                cacheHits
                cacheMisses
                hitRate
            }
        }
        """

        start_time = time.time()
        response = client.post("/graphql", json={"query": query})
        response_time = time.time() - start_time

        assert response.status_code == 200
        assert response_time < 0.5  # Complex queries should still be fast

    def test_bulk_mutation_performance(self, client):
        """Test performance of bulk operations."""
        mutation = """
        mutation {
            validateBulk(inputs: [
                {xpath: "/test/1", value: "value1"},
                {xpath: "/test/2", value: "value2"},
                {xpath: "/test/3", value: "value3"},
                {xpath: "/test/4", value: "value4"},
                {xpath: "/test/5", value: "value5"}
            ]) {
                valid
                errors
            }
        }
        """

        start_time = time.time()
        response = client.post("/graphql", json={"query": mutation})
        response_time = time.time() - start_time

        assert response.status_code == 200
        assert response_time < 0.2  # Bulk operations should be efficient


class TestGraphQLErrorHandling:
    """Test GraphQL error handling and validation."""

    def test_syntax_error_handling(self, client):
        """Test handling of GraphQL syntax errors."""
        query = "{ health"  # Missing closing brace

        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
        assert len(data["errors"]) > 0

    def test_validation_error_handling(self, client):
        """Test handling of GraphQL validation errors."""
        query = "{ nonExistentField }"

        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
        assert len(data["errors"]) > 0

    def test_type_error_handling(self, client):
        """Test handling of GraphQL type errors."""
        mutation = """
        mutation {
            validateField(input: {
                xpath: 123  # Should be string, not int
                value: "test"
            }) {
                valid
            }
        }
        """

        response = client.post("/graphql", json={"query": mutation})

        assert response.status_code == 200
        data = response.json()
        assert "errors" in data
        assert len(data["errors"]) > 0


class TestGraphQLIntegration:
    """Test GraphQL integration with existing systems."""

    def test_graphql_monitoring_integration(self, client):
        """Test that GraphQL queries are properly monitored."""
        # Make a GraphQL request
        query = "{ health }"
        response = client.post("/graphql", json={"query": query})

        assert response.status_code == 200

        # Check that metrics were recorded
        metrics_query = "{ performanceMetrics { totalRequests endpoints } }"
        metrics_response = client.post("/graphql", json={"query": metrics_query})

        assert metrics_response.status_code == 200
        data = metrics_response.json()
        metrics = data["data"]["performanceMetrics"]

        # Should have recorded the GraphQL request
        assert metrics["totalRequests"] > 0
        # Should include GraphQL endpoints in the list
        graphql_endpoints = [ep for ep in metrics["endpoints"] if "GraphQL" in ep]
        assert len(graphql_endpoints) > 0

    def test_graphql_cache_integration(self, client):
        """Test that GraphQL queries use the cache system."""
        # Make multiple identical queries
        query = "{ health }"

        for _ in range(3):
            response = client.post("/graphql", json={"query": query})
            assert response.status_code == 200

        # Check cache metrics
        cache_query = "{ cacheMetrics { cacheHits cacheMisses } }"
        response = client.post("/graphql", json={"query": cache_query})

        assert response.status_code == 200
        data = response.json()
        assert "cacheMetrics" in data["data"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])