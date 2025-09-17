"""In-memory caching for parsed schema data with TTL support."""

from __future__ import annotations

import hashlib
import time
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Any
from functools import lru_cache

from .models import RuleNode
from .xsd_parser import XSDParser, ParserConfig
from .merger import merge_rules


@dataclass
class CacheEntry:
    """Cache entry with TTL and versioning."""
    data: Any
    timestamp: float = field(default_factory=time.time)
    ttl: float = 3600.0  # 1 hour default
    etag: str = ""
    file_mtime: float = 0.0

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return time.time() - self.timestamp > self.ttl

    def is_stale(self, file_path: Path) -> bool:
        """Check if cache is stale based on file modification time."""
        if not file_path.exists():
            return True
        current_mtime = file_path.stat().st_mtime
        return current_mtime > self.file_mtime


class SchemaCache:
    """Thread-safe in-memory cache for parsed schemas with performance monitoring."""

    def __init__(self, default_ttl: float = 3600.0, enable_monitoring: bool = True):
        """Initialize cache with default TTL in seconds."""
        self.default_ttl = default_ttl
        self._cache: Dict[str, CacheEntry] = {}
        self._lock_dummy = None  # Placeholder for thread lock if needed
        self.enable_monitoring = enable_monitoring

        # Import monitor lazily to avoid circular imports
        if enable_monitoring:
            try:
                from .monitoring import get_monitor
                self._monitor = get_monitor()
            except ImportError:
                self.enable_monitoring = False
                self._monitor = None
        else:
            self._monitor = None

    def _make_key(self, *args) -> str:
        """Create cache key from arguments."""
        key_data = str(args).encode()
        return hashlib.md5(key_data).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """Get cached data if available and not expired."""
        start_time = time.time()
        entry = self._cache.get(key)

        if entry is None:
            if self.enable_monitoring and self._monitor:
                response_time = time.time() - start_time
                self._monitor.record_cache_miss(response_time)
            return None

        if entry.is_expired():
            del self._cache[key]
            if self.enable_monitoring and self._monitor:
                response_time = time.time() - start_time
                self._monitor.record_cache_miss(response_time)
                self._monitor.record_cache_eviction()
            return None

        if self.enable_monitoring and self._monitor:
            response_time = time.time() - start_time
            self._monitor.record_cache_hit(response_time)

        return entry.data

    def set(self, key: str, data: Any, ttl: Optional[float] = None,
            file_path: Optional[Path] = None) -> None:
        """Store data in cache with optional TTL override."""
        etag = ""
        file_mtime = 0.0

        if file_path and file_path.exists():
            file_mtime = file_path.stat().st_mtime
            with file_path.open("rb") as f:
                content = f.read()
                etag = hashlib.md5(content).hexdigest()

        self._cache[key] = CacheEntry(
            data=data,
            ttl=ttl or self.default_ttl,
            etag=etag,
            file_mtime=file_mtime
        )

        # Update cache size metrics
        if self.enable_monitoring and self._monitor:
            cache_size = len(self._cache)
            memory_usage = self._estimate_memory_usage()
            self._monitor.update_cache_size(cache_size, memory_usage)

    def invalidate(self, key: str) -> None:
        """Remove specific entry from cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()

    def _estimate_memory_usage(self) -> float:
        """Estimate memory usage of cache in MB."""
        try:
            total_size = sys.getsizeof(self._cache)
            for key, entry in self._cache.items():
                total_size += sys.getsizeof(key)
                total_size += sys.getsizeof(entry)
                total_size += sys.getsizeof(entry.data)
            return total_size / (1024 * 1024)  # Convert to MB
        except Exception:
            return 0.0  # Return 0 if size calculation fails

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get current cache statistics."""
        return {
            "cache_size": len(self._cache),
            "memory_usage_mb": self._estimate_memory_usage(),
            "default_ttl": self.default_ttl,
            "monitoring_enabled": self.enable_monitoring
        }

    def check_file_staleness(self, key: str, file_path: Path) -> bool:
        """Check if cached entry is stale based on file modification."""
        entry = self._cache.get(key)
        if entry is None:
            return True
        return entry.is_stale(file_path)


# Global cache instance
_schema_cache = SchemaCache()


class CachedSchemaParser:
    """Schema parser with caching support."""

    def __init__(self, cache: Optional[SchemaCache] = None,
                 parser_config: Optional[ParserConfig] = None):
        """Initialize with optional cache and parser config."""
        self.cache = cache or _schema_cache
        self.parser_config = parser_config or ParserConfig()

    def parse_xsd(self, xsd_path: Path, root_name: str = "HPXML",
                  force_refresh: bool = False) -> RuleNode:
        """Parse XSD with caching."""
        xsd_path = Path(xsd_path)
        cache_key = self.cache._make_key("xsd", str(xsd_path), root_name,
                                        str(self.parser_config.__dict__))

        # Check cache unless forced refresh
        if not force_refresh:
            # Check if file has been modified
            if not self.cache.check_file_staleness(cache_key, xsd_path):
                cached = self.cache.get(cache_key)
                if cached is not None:
                    return cached

        # Parse and cache
        parser = XSDParser(xsd_path, config=self.parser_config)
        result = parser.parse(root_name=root_name)
        self.cache.set(cache_key, result, file_path=xsd_path)
        return result

    def parse_schematron(self, sch_path: Path, force_refresh: bool = False) -> Dict[str, Any]:
        """Parse Schematron with caching."""
        sch_path = Path(sch_path)
        cache_key = self.cache._make_key("schematron", str(sch_path))

        # Check cache unless forced refresh
        if not force_refresh:
            if not self.cache.check_file_staleness(cache_key, sch_path):
                cached = self.cache.get(cache_key)
                if cached is not None:
                    return cached

        # Parse and cache - for now return empty dict as placeholder
        # In full implementation, would use SchematronParser
        result = {"rules": [], "source": str(sch_path)}
        self.cache.set(cache_key, result, file_path=sch_path)
        return result

    def parse_combined(self, xsd_path: Path, sch_path: Optional[Path] = None,
                      root_name: str = "HPXML", force_refresh: bool = False) -> RuleNode:
        """Parse and merge XSD and Schematron rules with caching."""
        xsd_path = Path(xsd_path)
        cache_key_parts = ["combined", str(xsd_path), root_name]
        if sch_path:
            cache_key_parts.append(str(sch_path))
        cache_key_parts.append(str(self.parser_config.__dict__))
        cache_key = self.cache._make_key(*cache_key_parts)

        # Check cache unless forced refresh
        if not force_refresh:
            stale = self.cache.check_file_staleness(cache_key, xsd_path)
            if sch_path and not stale:
                stale = self.cache.check_file_staleness(cache_key, sch_path)

            if not stale:
                cached = self.cache.get(cache_key)
                if cached is not None:
                    return cached

        # Parse XSD
        xsd_rules = self.parse_xsd(xsd_path, root_name, force_refresh)

        # Parse and merge Schematron if provided
        if sch_path:
            sch_rules = self.parse_schematron(sch_path, force_refresh)
            result = merge_rules(xsd_rules, sch_rules)
        else:
            result = xsd_rules

        # Cache the combined result
        self.cache.set(cache_key, result, file_path=xsd_path)
        return result

    def invalidate_all(self) -> None:
        """Clear all cached schemas."""
        self.cache.clear()


# Convenience function for lazy loading
@lru_cache(maxsize=4)
def get_cached_parser(parser_config_key: Optional[str] = None) -> CachedSchemaParser:
    """Get or create a cached parser instance.

    Args:
        parser_config_key: Optional string representation of parser config

    Returns:
        CachedSchemaParser instance
    """
    # Parse config key if provided
    config = ParserConfig()
    if parser_config_key:
        # Simple parsing of key=value pairs
        for pair in parser_config_key.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                if hasattr(config, key):
                    if key.startswith("max_"):
                        setattr(config, key, int(value))
                    else:
                        setattr(config, key, value.lower() == "true")

    return CachedSchemaParser(parser_config=config)