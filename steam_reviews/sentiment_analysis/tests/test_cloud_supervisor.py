from unittest.mock import Mock, patch

from sentiment_analysis.cloud import ManagedService


def test_failed_consumer_restarts_without_stopping_api():
    failed = Mock()
    failed.poll.return_value = 1
    running = Mock()
    running.poll.return_value = None
    consumer = ManagedService("consumer_730", ["consumer"], process=failed)
    api = ManagedService("dashboard_api", ["serve"], process=running)
    with patch("sentiment_analysis.cloud.subprocess.Popen") as spawn:
        consumer.tick(10)
        api.tick(10)
        spawn.assert_not_called()
        running.terminate.assert_not_called()
        assert consumer.retry_at == 12
        consumer.tick(12)
        assert spawn.call_count == 1
        assert consumer.process is spawn.return_value
        assert api.process is running


def test_repeated_failures_back_off_and_stable_uptime_resets_delay():
    service = ManagedService("consumer", ["consumer"])
    delays = []
    with patch("sentiment_analysis.cloud.subprocess.Popen") as spawn:
        spawn.return_value.poll.return_value = 1
        for _ in range(8):
            now = service.retry_at
            service.tick(now)
            service.tick(now + 1)
            delays.append(service.retry_at - now - 1)
        assert delays == [2, 4, 8, 16, 32, 60, 60, 60]
        service.tick(service.retry_at)
        service.tick(service.started_at + 301)
        assert service.failures == 1


def test_spawn_failure_is_retried_and_stopping_does_not_restart():
    service = ManagedService("consumer", ["consumer"])
    with patch("sentiment_analysis.cloud.subprocess.Popen", side_effect=OSError) as spawn:
        service.tick(10)
        assert service.retry_at == 12
        service.tick(11)
        assert spawn.call_count == 1
        service.stop()
        assert spawn.call_count == 1
