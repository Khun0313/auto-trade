"""core/auth.py 단위 테스트."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 테스트 시 auto_trader를 sys.path에 추가
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.auth import KISAuth


@pytest.fixture
def mock_env(monkeypatch, tmp_path):
    """테스트용 환경 변수 및 설정 파일 세팅."""
    monkeypatch.setenv("KIS_APP_KEY", "test_key")
    monkeypatch.setenv("KIS_APP_SECRET", "test_secret")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678-01")
    return tmp_path


@patch("core.auth.KISAuth._load_settings")
@patch("core.auth.KISAuth._try_load_cached_token")
def test_get_headers_includes_token(mock_cache, mock_settings):
    """get_headers가 올바른 헤더를 반환하는지 확인."""
    mock_settings.return_value = None
    mock_cache.return_value = None

    auth = KISAuth.__new__(KISAuth)
    auth.app_key = "test_key"
    auth.app_secret = "test_secret"
    auth.access_token = "mock_token"
    auth.token_expired_at = datetime.now() + timedelta(hours=12)
    auth.refresh_before_hours = 1
    auth.mode = "paper"
    auth.base_url = "https://openapivts.koreainvestment.com:29443"

    headers = auth.get_headers("VTTC0802U")
    assert headers["authorization"] == "Bearer mock_token"
    assert headers["tr_id"] == "VTTC0802U"
    assert headers["appkey"] == "test_key"


@patch("core.auth.KISAuth._load_settings")
@patch("core.auth.KISAuth._try_load_cached_token")
def test_token_validity_check(mock_cache, mock_settings):
    """만료 시간 기반 토큰 유효성 체크."""
    mock_settings.return_value = None
    mock_cache.return_value = None

    auth = KISAuth.__new__(KISAuth)
    auth.refresh_before_hours = 1

    # 유효한 토큰
    auth.access_token = "valid"
    auth.token_expired_at = datetime.now() + timedelta(hours=12)
    assert auth._is_token_valid() is True

    # 만료 임박 토큰 (1시간 이내)
    auth.token_expired_at = datetime.now() + timedelta(minutes=30)
    assert auth._is_token_valid() is False

    # 토큰 없음
    auth.access_token = ""
    assert auth._is_token_valid() is False


@patch("core.auth.KISAuth._load_settings")
@patch("core.auth.KISAuth._try_load_cached_token")
@patch("core.auth.requests.post")
def test_issue_token(mock_post, mock_cache, mock_settings):
    """토큰 발급이 정상 동작하는지 확인."""
    mock_settings.return_value = None
    mock_cache.return_value = None

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "access_token": "new_token_123",
        "expires_in": 86400,
    }
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    auth = KISAuth.__new__(KISAuth)
    auth.app_key = "key"
    auth.app_secret = "secret"
    auth.base_url = "https://test.api.com"
    auth.mode = "paper"
    auth.refresh_before_hours = 1
    auth.access_token = ""
    auth.token_expired_at = None

    # _save_token_cache를 mock
    auth._save_token_cache = MagicMock()

    auth._issue_token()

    assert auth.access_token == "new_token_123"
    assert auth.token_expired_at is not None
    auth._save_token_cache.assert_called_once()
