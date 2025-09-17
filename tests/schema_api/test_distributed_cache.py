"""Comprehensive tests for distributed caching with Redis backend."""

import json
import os
import pickle
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Any, Dict

import pytest

try:
    import fakeredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from hpxml_schema_api.cache import (
    DistributedCache,
    SchemaCache,
    CachedSchemaParser,
    get_cached_parser,
    get_cache_instance,
    _get_default_cache
)
from hpxml_schema_api.models import RuleNode
from hpxml_schema_api.xsd_parser import ParserConfig


@pytest.fixture
def fake_redis():
    """Provide a fake Redis instance for testing."""
    if not REDIS_AVAILABLE:
        pytest.skip("fakeredis not available")
    return fakeredis.FakeRedis(decode_responses=False)


@pytest.fixture
def temp_file():
    """Create a temporary file for testing file staleness detection."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("test content")
        temp_path = Path(f.name)

    yield temp_path

    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def sample_rule_node():
    """Create a sample RuleNode for testing."""
    return RuleNode(
        name="TestNode",
        xpath="/test",
        kind="field",
        data_type="string",
        description="Test node for cache testing"
    )


class TestDistributedCache:
    """Test cases for DistributedCache class."""

    def test_init_with_redis_available(self, fake_redis):
        """Test initialization when Redis is available."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache(redis_url="redis://test:6379/0")

            assert cache._redis_available is True
            assert cache._redis is fake_redis
            assert cache.default_ttl == 3600.0
            assert cache.redis_prefix == "hpxml:"

    def test_init_with_redis_unavailable(self):
        """Test initialization when Redis is unavailable."""
        with patch('redis.from_url', side_effect=Exception("Connection failed")):
            cache = DistributedCache(redis_url="redis://nonexistent:6379/0")

            assert cache._redis_available is False
            assert cache._redis is None
            assert isinstance(cache.fallback_cache, SchemaCache)

    def test_init_with_custom_config(self, fake_redis):
        """Test initialization with custom configuration."""
        fallback = SchemaCache(default_ttl=1800.0)

        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache(
                default_ttl=7200.0,
                redis_prefix="custom:",
                fallback_cache=fallback
            )

            assert cache.default_ttl == 7200.0
            assert cache.redis_prefix == "custom:"
            assert cache.fallback_cache is fallback

    def test_serialize_deserialize_pickle(self, sample_rule_node):
        """Test serialization/deserialization with pickle."""
        cache = DistributedCache()

        # Test pickle serialization
        serialized = cache._serialize_data(sample_rule_node)
        assert isinstance(serialized, bytes)

        # Test pickle deserialization
        deserialized = cache._deserialize_data(serialized)
        assert isinstance(deserialized, RuleNode)
        assert deserialized.name == sample_rule_node.name
        assert deserialized.xpath == sample_rule_node.xpath

    def test_serialize_deserialize_json_fallback(self):
        """Test serialization/deserialization with JSON fallback."""
        cache = DistributedCache()
        test_data = {"key": "value", "number": 42}

        # Mock pickle to fail and force JSON fallback
        with patch('pickle.dumps', side_effect=Exception("Pickle failed")):
            serialized = cache._serialize_data(test_data)
            assert isinstance(serialized, bytes)

            deserialized = cache._deserialize_data(serialized)
            assert deserialized == test_data

    def test_serialize_deserialize_string_fallback(self):
        """Test serialization/deserialization with string fallback."""
        cache = DistributedCache()
        test_data = "simple string"

        # Mock both pickle and JSON to fail
        with patch('pickle.dumps', side_effect=Exception("Pickle failed")), \
             patch('json.dumps', side_effect=Exception("JSON failed")):

            serialized = cache._serialize_data(test_data)
            assert isinstance(serialized, bytes)
            assert serialized == b"simple string"

    def test_get_set_redis_available(self, fake_redis):
        """Test get/set operations when Redis is available."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Test set
            test_data = {"test": "data"}
            cache.set("test_key", test_data, ttl=3600)

            # Verify data was stored in Redis
            redis_key = cache._make_key("test_key")
            assert fake_redis.exists(redis_key)

            # Test get
            retrieved = cache.get("test_key")
            assert retrieved == test_data

    def test_get_set_redis_unavailable(self):
        """Test get/set operations when Redis is unavailable."""
        with patch('redis.from_url', side_effect=Exception("Connection failed")):
            cache = DistributedCache()

            # Should fallback to local cache
            test_data = {"test": "data"}
            cache.set("test_key", test_data)

            retrieved = cache.get("test_key")
            assert retrieved == test_data

    def test_get_nonexistent_key(self, fake_redis):
        """Test getting a non-existent key."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            result = cache.get("nonexistent_key")
            assert result is None

    def test_ttl_expiration(self, fake_redis):
        """Test TTL expiration in Redis."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Set with short TTL
            cache.set("expire_key", "test_data", ttl=1)

            # Should be available immediately
            assert cache.get("expire_key") == "test_data"

            # Wait for expiration (fakeredis handles TTL)
            time.sleep(1.1)

            # Should be expired
            assert cache.get("expire_key") is None

    def test_file_metadata_tracking(self, fake_redis, temp_file):
        """Test file metadata tracking with Redis."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Set with file path
            cache.set("file_key", "test_data", file_path=temp_file)

            # Check that metadata was stored
            redis_key = cache._make_key("file_key")
            metadata_key = f"{redis_key}:meta"
            assert fake_redis.exists(metadata_key)

            # Verify file staleness check
            assert not cache.check_file_staleness("file_key", temp_file)

    def test_file_staleness_detection(self, fake_redis, temp_file):
        """Test file staleness detection."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Set initial data
            cache.set("stale_key", "old_data", file_path=temp_file)
            assert not cache.check_file_staleness("stale_key", temp_file)

            # Modify file (simulate by waiting and touching)
            time.sleep(0.1)
            temp_file.touch()

            # Should now be stale
            assert cache.check_file_staleness("stale_key", temp_file)

    def test_invalidate_redis_and_fallback(self, fake_redis):
        """Test invalidation in both Redis and fallback cache."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Set data
            cache.set("invalid_key", "test_data")
            assert cache.get("invalid_key") == "test_data"

            # Invalidate
            cache.invalidate("invalid_key")

            # Should be gone
            assert cache.get("invalid_key") is None

    def test_clear_all_caches(self, fake_redis):
        """Test clearing all cache entries."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Set multiple keys
            cache.set("key1", "data1")
            cache.set("key2", "data2")
            cache.set("key3", "data3")

            # Verify they exist
            assert cache.get("key1") == "data1"
            assert cache.get("key2") == "data2"

            # Clear all
            cache.clear()

            # All should be gone
            assert cache.get("key1") is None
            assert cache.get("key2") is None
            assert cache.get("key3") is None

    def test_redis_error_fallback_during_get(self, fake_redis):
        """Test fallback behavior when Redis fails during get operation."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Initially set data in fallback cache
            cache.fallback_cache.set("fallback_key", "fallback_data")

            # Mock Redis to fail during get
            fake_redis.get = Mock(side_effect=Exception("Redis get failed"))

            # Should fallback to local cache
            result = cache.get("fallback_key")
            assert result == "fallback_data"
            assert not cache._redis_available  # Should mark Redis as unavailable

    def test_redis_error_fallback_during_set(self, fake_redis):
        """Test fallback behavior when Redis fails during set operation."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Mock Redis to fail during set
            fake_redis.setex = Mock(side_effect=Exception("Redis set failed"))

            # Should fallback to local cache
            cache.set("fallback_key", "fallback_data")

            # Data should be in fallback cache
            assert cache.fallback_cache.get("fallback_key") == "fallback_data"
            assert not cache._redis_available  # Should mark Redis as unavailable

    def test_get_cache_stats_redis_available(self, fake_redis):
        """Test cache statistics when Redis is available."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Add some test data
            cache.set("stats_key1", "data1")
            cache.set("stats_key2", "data2")

            stats = cache.get_cache_stats()

            assert stats["redis_available"] is True
            assert "fallback_stats" in stats
            # Redis stats might fail in fake environment, so just check they exist or have error
            if "redis_stats" in stats:
                assert isinstance(stats["redis_stats"], dict)
            if "redis_key_count" in stats:
                assert isinstance(stats["redis_key_count"], int)

    def test_get_cache_stats_redis_unavailable(self):
        """Test cache statistics when Redis is unavailable."""
        with patch('redis.from_url', side_effect=Exception("Connection failed")):
            cache = DistributedCache()

            stats = cache.get_cache_stats()

            assert stats["redis_available"] is False
            assert "fallback_stats" in stats
            assert "redis_stats" not in stats

    def test_concurrent_access_simulation(self, fake_redis):
        """Simulate concurrent access patterns."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Simulate multiple workers setting/getting data
            keys = [f"concurrent_key_{i}" for i in range(10)]
            values = [f"concurrent_data_{i}" for i in range(10)]

            # Set all values
            for key, value in zip(keys, values):
                cache.set(key, value)

            # Get all values
            retrieved_values = []
            for key in keys:
                retrieved_values.append(cache.get(key))

            assert retrieved_values == values


class TestEnvironmentConfiguration:
    """Test environment variable configuration for distributed caching."""

    def setup_method(self):
        """Clear global cache state before each test."""
        import hpxml_schema_api.cache
        hpxml_schema_api.cache._distributed_cache = None

    def test_default_cache_type_local(self):
        """Test default cache type is local."""
        with patch.dict(os.environ, {}, clear=True):
            cache = _get_default_cache()
            assert isinstance(cache, SchemaCache)

    def test_cache_type_distributed_env_var(self, fake_redis):
        """Test distributed cache type via environment variable."""
        with patch.dict(os.environ, {"HPXML_CACHE_TYPE": "distributed"}), \
             patch('redis.from_url', return_value=fake_redis):

            cache = _get_default_cache()
            assert isinstance(cache, DistributedCache)

    def test_redis_url_env_var(self, fake_redis):
        """Test Redis URL via environment variable."""
        test_url = "redis://test-server:6379/1"

        with patch.dict(os.environ, {"REDIS_URL": test_url}), \
             patch('redis.from_url', return_value=fake_redis) as mock_redis:

            cache = _get_default_cache()
            assert isinstance(cache, DistributedCache)
            mock_redis.assert_called_once()
            # Verify the URL was passed correctly
            call_args = mock_redis.call_args[0]
            assert test_url in call_args

    def test_cache_ttl_env_var(self, fake_redis):
        """Test cache TTL configuration via environment variable."""
        with patch.dict(os.environ, {"HPXML_CACHE_TTL": "7200", "HPXML_CACHE_TYPE": "distributed"}), \
             patch('redis.from_url', return_value=fake_redis):

            cache = _get_default_cache()
            assert cache.default_ttl == 7200.0

    def test_redis_prefix_env_var(self, fake_redis):
        """Test Redis key prefix configuration via environment variable."""
        with patch.dict(os.environ, {"HPXML_REDIS_PREFIX": "custom_prefix:", "REDIS_URL": "redis://test:6379"}), \
             patch('redis.from_url', return_value=fake_redis):

            cache = _get_default_cache()
            assert cache.redis_prefix == "custom_prefix:"


class TestCachedSchemaParserIntegration:
    """Test integration of distributed cache with CachedSchemaParser."""

    def test_parser_with_distributed_cache(self, fake_redis, temp_file):
        """Test schema parser with distributed cache backend."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()
            parser = CachedSchemaParser(cache=cache)

            assert isinstance(parser.cache, DistributedCache)

    def test_get_cached_parser_with_cache_type_override(self, fake_redis):
        """Test get_cached_parser with cache type override."""
        with patch('redis.from_url', return_value=fake_redis):
            # Test local cache override
            parser_local = get_cached_parser(cache_type="local")
            assert isinstance(parser_local.cache, SchemaCache)

            # Test distributed cache override
            parser_distributed = get_cached_parser(cache_type="distributed")
            assert isinstance(parser_distributed.cache, DistributedCache)

    def test_get_cache_instance_function(self, fake_redis):
        """Test get_cache_instance function."""
        with patch.dict(os.environ, {"HPXML_CACHE_TYPE": "distributed"}), \
             patch('redis.from_url', return_value=fake_redis):

            cache = get_cache_instance()
            assert isinstance(cache, DistributedCache)


class TestPerformanceBenchmarks:
    """Performance benchmark tests for distributed cache."""

    def test_cache_response_time_redis(self, fake_redis):
        """Test cache response time with Redis backend."""
        with patch('redis.from_url', return_value=fake_redis):
            cache = DistributedCache()

            # Warm up
            test_data = {"benchmark": "data"}
            cache.set("perf_key", test_data)

            # Measure get operation
            start_time = time.time()
            result = cache.get("perf_key")
            response_time = time.time() - start_time

            assert result == test_data
            # Should be fast (allowing for test environment overhead)
            assert response_time < 0.1  # 100ms should be plenty for fake Redis

    def test_cache_response_time_fallback(self):
        """Test cache response time with fallback to local cache."""
        with patch('redis.from_url', side_effect=Exception("Connection failed")):
            cache = DistributedCache()

            # Should use fallback cache
            test_data = {"benchmark": "data"}
            cache.set("perf_key", test_data)

            # Measure get operation
            start_time = time.time()
            result = cache.get("perf_key")
            response_time = time.time() - start_time

            assert result == test_data
            # Local cache should be very fast
            assert response_time < 0.01  # 10ms should be plenty for in-memory

    def test_serialization_performance(self, sample_rule_node):
        """Test serialization/deserialization performance."""
        cache = DistributedCache()

        # Test pickle performance
        start_time = time.time()
        serialized = cache._serialize_data(sample_rule_node)
        serialize_time = time.time() - start_time

        start_time = time.time()
        deserialized = cache._deserialize_data(serialized)
        deserialize_time = time.time() - start_time

        assert isinstance(deserialized, RuleNode)
        # Serialization should be fast
        assert serialize_time < 0.01  # 10ms
        assert deserialize_time < 0.01  # 10ms


@pytest.mark.skipif(not REDIS_AVAILABLE, reason="fakeredis not available")
def test_full_integration_scenario(fake_redis, temp_file):
    """Test full integration scenario with multiple operations."""
    with patch('redis.from_url', return_value=fake_redis):
        # Create distributed cache
        cache = DistributedCache(redis_prefix="integration_test:")

        # Create parser with distributed cache
        parser = CachedSchemaParser(cache=cache)

        # Test data operations
        test_data = RuleNode(
            name="IntegrationTest",
            xpath="/integration/test",
            kind="field",
            data_type="string",
            description="Integration test node"
        )

        # Store with file metadata
        cache.set("integration_key", test_data, file_path=temp_file)

        # Retrieve and verify
        retrieved = cache.get("integration_key")
        assert isinstance(retrieved, RuleNode)
        assert retrieved.name == "IntegrationTest"

        # Test file staleness
        assert not cache.check_file_staleness("integration_key", temp_file)

        # Get comprehensive stats
        stats = cache.get_cache_stats()
        assert stats["redis_available"] is True
        # Redis key count might not be available in fake environment
        if "redis_key_count" in stats:
            assert stats["redis_key_count"] >= 1

        # Test cleanup
        cache.clear()
        assert cache.get("integration_key") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])