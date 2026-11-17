import base64
import io
from unittest.mock import patch

import pytest

from sentiment_analysis.client import ApiClient, ApiError
from sentiment_analysis.api import _source_health
from sentiment_analysis.api import create_app
from fastapi.testclient import TestClient


def test_cloud_credentials_are_headers_not_urls(monkeypatch):
    monkeypatch.setenv("STEAM_API_USER", "test-user")
    monkeypatch.setenv("STEAM_API_PASSWORD", "test-password")
    with patch("sentiment_analysis.client.urlopen", return_value=io.BytesIO(b'{"app_id":730}')) as opened:
        assert ApiClient("https://collector.example", 730).health() == {"app_id": 730}
    request = opened.call_args.args[0]
    assert request.full_url == "https://collector.example/v1/health?app_id=730"
    expected = base64.b64encode(b"test-user:test-password").decode()
    assert request.get_header("Authorization") == "Basic " + expected
    assert "Authorization" not in request.headers


def test_local_sample_does_not_receive_cloud_credentials(monkeypatch):
    monkeypatch.setenv("STEAM_API_USER", "test-user")
    monkeypatch.setenv("STEAM_API_PASSWORD", "test-password")
    with patch("sentiment_analysis.client.urlopen", return_value=io.BytesIO(b'{}')) as opened:
        ApiClient("http://127.0.0.1:8200").games()
    assert opened.call_args.args[0].get_header("Authorization") is None


@pytest.mark.parametrize("address", ["http://collector.example", "https://user:pass@collector.example"])
def test_unsafe_credential_transport_rejected(address):
    with pytest.raises(ApiError):
        ApiClient(address).games()


def test_missing_credentials_reported(monkeypatch):
    monkeypatch.delenv("STEAM_API_USER", raising=False)
    monkeypatch.delenv("STEAM_API_PASSWORD", raising=False)
    with pytest.raises(ApiError, match="STEAM_API_USER"):
        ApiClient("https://collector.example").games()


def test_api_health_uses_the_same_authenticated_client(monkeypatch):
    monkeypatch.setenv("STEAM_API_USER", "test-user")
    monkeypatch.setenv("STEAM_API_PASSWORD", "test-password")
    with patch("sentiment_analysis.client.urlopen", return_value=io.BytesIO(b'{"app_id":730}')) as opened:
        assert _source_health(730, "https://collector.example") == {"app_id": 730}
    assert opened.call_args.args[0].get_header("Authorization")


def test_directory_proxy_keeps_credentials_in_backend(monkeypatch):
    monkeypatch.setenv("STEAM_API_USER", "test-user")
    monkeypatch.setenv("STEAM_API_PASSWORD", "test-password")
    payload = b'{"schema_version":"1.0","mode":"live","games":[]}'
    with patch("sentiment_analysis.client.urlopen", return_value=io.BytesIO(payload)) as opened:
        response = TestClient(create_app(collector_url="https://collector.example")).get("/v1/games")
    assert response.status_code == 200
    assert response.json()["mode"] == "live"
    assert opened.call_args.args[0].get_header("Authorization")
    assert "test-password" not in response.text


def test_directory_proxy_reports_unconfigured_collector():
    assert TestClient(create_app(collector_url=None)).get("/v1/games").status_code == 503
