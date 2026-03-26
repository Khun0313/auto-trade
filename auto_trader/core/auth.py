"""한국투자증권 API 인증/토큰 관리 모듈."""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

from utils.logger import get_logger

logger = get_logger("auth")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
TOKEN_CACHE_PATH = BASE_DIR / "data" / "token.json"


class KISAuth:
    """한국투자증권 Open API 인증 관리자."""

    def __init__(self):
        load_dotenv(BASE_DIR / ".env")
        self._load_settings()

        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.account_no = os.getenv("KIS_ACCOUNT_NO", "")
        self.account_product_code = os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01")

        self.access_token: str = ""
        self.token_expired_at: datetime | None = None

        self._try_load_cached_token()

    def _load_settings(self):
        """settings.yaml에서 설정을 로드한다."""
        with open(CONFIG_DIR / "settings.yaml", "r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)

        self.mode = settings["system"]["mode"]  # paper / live
        api_cfg = settings["api"][self.mode]
        self.base_url = api_cfg["base_url"]
        self.ws_url = api_cfg["ws_url"]

        token_cfg = settings["token"]
        self.refresh_before_hours = token_cfg["refresh_before_hours"]

    # ------------------------------------------------------------------
    # 토큰 발급 / 갱신
    # ------------------------------------------------------------------

    def get_token(self) -> str:
        """유효한 액세스 토큰을 반환한다. 필요 시 자동 갱신."""
        if self._is_token_valid():
            return self.access_token

        logger.info("토큰이 만료되었거나 없습니다. 새로 발급합니다.")
        self._issue_token()
        return self.access_token

    def _issue_token(self):
        """POST /oauth2/tokenP 로 새 토큰을 발급받는다."""
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        try:
            resp = requests.post(url, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            self.access_token = data["access_token"]
            # 토큰 유효기간: 발급 시점 + 약 24시간
            expires_in = int(data.get("expires_in", 86400))
            self.token_expired_at = datetime.now() + timedelta(seconds=expires_in)

            self._save_token_cache()
            logger.info(
                "토큰 발급 완료 (만료: %s, 모드: %s)",
                self.token_expired_at.strftime("%Y-%m-%d %H:%M:%S"),
                self.mode,
            )
        except requests.RequestException as e:
            logger.error("토큰 발급 실패: %s", e)
            raise

    def _is_token_valid(self) -> bool:
        """토큰이 아직 유효한지 확인한다 (만료 N시간 전이면 무효 처리)."""
        if not self.access_token or not self.token_expired_at:
            return False
        margin = timedelta(hours=self.refresh_before_hours)
        return datetime.now() < (self.token_expired_at - margin)

    # ------------------------------------------------------------------
    # 토큰 캐시 (파일)
    # ------------------------------------------------------------------

    def _save_token_cache(self):
        """토큰을 JSON 파일에 캐싱한다."""
        TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache = {
            "access_token": self.access_token,
            "expired_at": self.token_expired_at.isoformat(),
            "mode": self.mode,
        }
        TOKEN_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        logger.debug("토큰 캐시 저장: %s", TOKEN_CACHE_PATH)

    def _try_load_cached_token(self):
        """캐시된 토큰을 로드한다. 모드가 다르면 무시."""
        if not TOKEN_CACHE_PATH.exists():
            return

        try:
            cache = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            if cache.get("mode") != self.mode:
                logger.info("캐시된 토큰 모드(%s)가 현재(%s)와 다릅니다. 무시.", cache.get("mode"), self.mode)
                return

            self.access_token = cache["access_token"]
            self.token_expired_at = datetime.fromisoformat(cache["expired_at"])

            if self._is_token_valid():
                logger.info("캐시된 토큰 로드 (만료: %s)", self.token_expired_at)
            else:
                logger.info("캐시된 토큰이 만료되었습니다.")
                self.access_token = ""
                self.token_expired_at = None
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("토큰 캐시 파일 손상: %s", e)

    # ------------------------------------------------------------------
    # 공통 헤더
    # ------------------------------------------------------------------

    def get_headers(self, tr_id: str) -> dict:
        """API 호출용 공통 헤더를 반환한다."""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }

    def get_ws_approval_key(self) -> str:
        """WebSocket 접속용 approval key를 발급받는다."""
        url = f"{self.base_url}/oauth2/Approval"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }
        try:
            resp = requests.post(url, json=body, timeout=10)
            resp.raise_for_status()
            return resp.json()["approval_key"]
        except requests.RequestException as e:
            logger.error("WebSocket approval key 발급 실패: %s", e)
            raise
