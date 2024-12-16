import os
import pytest

try:
    import redis
    from testcontainers.redis import RedisContainer


    @pytest.fixture(scope="module")
    def redis_container():
        with RedisContainer("redis:alpine") as redis_container:
            yield redis_container


    @pytest.fixture(scope="module")
    def redis_client(redis_container):
        return redis.Redis(host=redis_container.get_container_host_ip(), port=redis_container.get_exposed_port(6379))

except ImportError:
    pass

def _slow_tests_enabled():
    return os.getenv("SLOW_TESTS") in ("true", "1", "enabled")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow (skipped unless SLOW_TESTS=true/1/enabled)"
    )
    # Register the slow marker
    pytest.mark.slow = pytest.mark.skipif(
        not _slow_tests_enabled(),
        reason="Test skipped because SLOW_TESTS environment variable not set to true, 1 or enabled"
    )
