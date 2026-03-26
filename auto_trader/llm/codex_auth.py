"""ChatGPT Codex OAuth 토큰 관리.

Codex CLI가 저장한 ~/.codex/auth.json 토큰을 읽어
Python 코드에서 ChatGPT OAuth 방식으로 API를 호출할 수 있게 한다.

토큰 갱신 흐름:
  1. access_token 만료 감지 (만료 5분 전부터 선제 갱신)
  2. refresh_token으로 새 access_token 발급 (OpenAI OAuth 엔드포인트)
  3. 갱신 실패 시 → codex login으로 재로그인 유도

사용법:
    python -m llm.codex_auth           # 상태 확인 + 필요시 로그인 안내
    python -m llm.codex_auth --login   # 강제 재로그인
    python -m llm.codex_auth --status  # 상태만 출력
    python -m llm.codex_auth --refresh # 토큰 수동 갱신
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from utils.logger import get_logger
    logger = get_logger("codex_auth")
except ImportError:
    import logging
    logger = logging.getLogger("codex_auth")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# ──────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────

AUTH_FILE      = Path.home() / ".codex" / "auth.json"
AUTH_META_FILE = Path.home() / ".codex" / "auth_meta.json"  # 자체 추적 메타데이터

# OpenAI Responses API 엔드포인트 (Codex CLI 실제 사용 엔드포인트)
CODEX_API_BASE        = "https://api.openai.com/v1"
CODEX_RESPONSES_URL   = f"{CODEX_API_BASE}/responses"

# OpenAI OAuth 토큰 갱신 엔드포인트
OAUTH_TOKEN_ENDPOINT  = "https://auth.openai.com/oauth/token"

# access token: 만료 5분 전부터 선제 갱신
TOKEN_EXPIRY_BUFFER_SEC = 300

# refresh token: 만료 7일 전부터 Discord 경고
REFRESH_WARN_DAYS = 7

# refresh token 추정 유효기간 (auth.json에 명시 없을 때 사용)
# OpenAI는 통상 60일이지만 보수적으로 45일로 설정
REFRESH_TOKEN_DEFAULT_LIFETIME_DAYS = 45


# ──────────────────────────────────────────────────────────────
# auth.json 읽기 / 쓰기
# ──────────────────────────────────────────────────────────────

def _read_auth_file() -> dict:
    """~/.codex/auth.json을 읽어 dict로 반환한다."""
    if not AUTH_FILE.exists():
        return {}
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("auth.json 읽기 실패: %s", e)
        return {}


def _write_auth_file(auth: dict) -> bool:
    """갱신된 토큰 정보를 auth.json에 저장한다."""
    try:
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(auth, f, indent=2, ensure_ascii=False)
        return True
    except OSError as e:
        logger.error("auth.json 저장 실패: %s", e)
        return False


# ──────────────────────────────────────────────────────────────
# auth_meta.json: refresh token 발급 시각 자체 추적
# ──────────────────────────────────────────────────────────────

def _read_meta() -> dict:
    """~/.codex/auth_meta.json을 읽는다."""
    if not AUTH_META_FILE.exists():
        return {}
    try:
        with open(AUTH_META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_meta(meta: dict):
    """auth_meta.json을 저장한다."""
    try:
        AUTH_META_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUTH_META_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.error("auth_meta.json 저장 실패: %s", e)


def _record_refresh_token_issued():
    """refresh token 발급 시각을 auth_meta.json에 기록한다.

    codex login 또는 토큰 갱신 시 호출해야 한다.
    """
    meta = _read_meta()
    meta["refresh_token_issued_at"] = time.time()
    meta["refresh_token_estimated_expiry"] = (
        time.time() + REFRESH_TOKEN_DEFAULT_LIFETIME_DAYS * 86400
    )
    _write_meta(meta)


# ──────────────────────────────────────────────────────────────
# 토큰 조회
# ──────────────────────────────────────────────────────────────

def _get_tokens_dict(auth: dict) -> dict:
    """auth.json에서 토큰 딕셔너리를 반환한다.

    Codex CLI는 {"tokens": {"access_token": ..., "refresh_token": ...}} 형태로 저장.
    구버전은 최상위에 바로 저장하는 경우도 있으므로 두 경우 모두 처리한다.
    """
    return auth.get("tokens") or auth


def get_access_token() -> Optional[str]:
    """access token을 반환한다. 없으면 None."""
    auth = _read_auth_file()
    tokens = _get_tokens_dict(auth)
    for key in ("access_token", "accessToken", "token"):
        val = tokens.get(key)
        if val:
            return val
    return None


def get_refresh_token() -> Optional[str]:
    """refresh token을 반환한다. 없으면 None."""
    auth = _read_auth_file()
    tokens = _get_tokens_dict(auth)
    for key in ("refresh_token", "refreshToken"):
        val = tokens.get(key)
        if val:
            return val
    return None


def get_openai_api_key_from_auth() -> Optional[str]:
    """auth.json에 저장된 OPENAI_API_KEY를 반환한다. 없으면 None.

    Codex CLI는 API Key 방식 로그인 시 auth.json에 OPENAI_API_KEY를 저장한다.
    """
    auth = _read_auth_file()
    return auth.get("OPENAI_API_KEY") or None


def _get_client_id() -> Optional[str]:
    """client_id를 auth.json에서 읽는다."""
    auth = _read_auth_file()
    tokens = _get_tokens_dict(auth)
    for key in ("client_id", "clientId"):
        val = tokens.get(key)
        if val:
            return val
    return None


# ──────────────────────────────────────────────────────────────
# 만료 확인
# ──────────────────────────────────────────────────────────────

def _parse_expires_at(auth: dict) -> Optional[float]:
    """만료 시각을 Unix timestamp(float)로 반환한다. 없으면 None."""
    expires_at = auth.get("expiresAt") or auth.get("expires_at")
    if expires_at is None:
        return None
    try:
        if isinstance(expires_at, (int, float)):
            # Codex CLI는 밀리초로 저장하기도 함 (13자리)
            if expires_at > 1e11:
                return expires_at / 1000.0
            return float(expires_at)
        # ISO8601 문자열
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        return expiry.timestamp()
    except Exception:
        return None


def is_token_expired() -> bool:
    """access token이 만료되었거나 곧 만료되는지 확인한다.

    만료 정보가 없으면 False(유효하다고 가정)를 반환한다.
    """
    auth = _read_auth_file()
    expiry_ts = _parse_expires_at(auth)
    if expiry_ts is None:
        return False
    return time.time() + TOKEN_EXPIRY_BUFFER_SEC >= expiry_ts


def remaining_seconds() -> Optional[float]:
    """access token 남은 유효 시간(초)을 반환한다. 정보 없으면 None."""
    auth = _read_auth_file()
    expiry_ts = _parse_expires_at(auth)
    if expiry_ts is None:
        return None
    return max(0.0, expiry_ts - time.time())


# ──────────────────────────────────────────────────────────────
# Refresh token 만료 추적
# ──────────────────────────────────────────────────────────────

def get_refresh_token_expiry_ts() -> Optional[float]:
    """refresh token 만료 예상 시각(Unix timestamp)을 반환한다.

    우선순위:
      1. auth.json에 refreshTokenExpiresAt / refresh_token_expires_at 필드가 있으면 사용
      2. auth_meta.json의 자체 추적값 사용
      3. 둘 다 없으면 None
    """
    # 1) auth.json에 명시된 경우 (Codex CLI가 저장했을 때)
    auth = _read_auth_file()
    for key in ("refreshTokenExpiresAt", "refresh_token_expires_at",
                "refreshTokenExpiry",   "refresh_token_expiry"):
        val = auth.get(key)
        if val:
            expiry = _parse_ts(val)
            if expiry:
                return expiry

    # 2) auth_meta.json 자체 추적값
    meta = _read_meta()
    estimated = meta.get("refresh_token_estimated_expiry")
    if estimated:
        return float(estimated)

    return None


def _parse_ts(val) -> Optional[float]:
    """다양한 형식의 타임스탬프를 Unix timestamp(float)로 변환한다."""
    try:
        if isinstance(val, (int, float)):
            # 밀리초(13자리) vs 초(10자리) 구분
            return val / 1000.0 if val > 1e11 else float(val)
        ts = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return ts.timestamp()
    except Exception:
        return None


def refresh_token_remaining_days() -> Optional[float]:
    """refresh token 남은 유효 일수를 반환한다. 정보 없으면 None."""
    expiry_ts = get_refresh_token_expiry_ts()
    if expiry_ts is None:
        return None
    remaining_sec = expiry_ts - time.time()
    return remaining_sec / 86400.0


def is_refresh_token_expiring_soon(warn_days: int = REFRESH_WARN_DAYS) -> bool:
    """refresh token이 warn_days일 이내에 만료되는지 확인한다."""
    remaining = refresh_token_remaining_days()
    if remaining is None:
        return False  # 추적 정보 없음 — 경고 안 함
    return remaining <= warn_days


async def check_refresh_token_expiry_warning(bot=None):
    """매일 아침 호출: refresh token 만료 7일 전 Discord 경고를 발송한다.

    Args:
        bot: TradingBot 인스턴스. None이면 Discord 전송 없이 로그만 남긴다.
    """
    remaining = refresh_token_remaining_days()

    if remaining is None:
        # auth_meta.json이 없으면 최초 생성 (시스템 시작 후 첫 체크)
        if is_logged_in():
            logger.info(
                "refresh token 발급 시각 정보 없음 — auth_meta.json 초기 생성"
            )
            _record_refresh_token_issued()
        return

    if remaining <= 0:
        # 이미 만료 (refresh_access_token 실패 시 별도 처리됨)
        return

    if remaining > REFRESH_WARN_DAYS:
        logger.debug("refresh token 잔여 %.1f일 — 정상", remaining)
        return

    # ── 경고 구간 진입 ──
    days_int = int(remaining)
    logger.warning("refresh token 만료 %d일 전 — 재로그인 권장", days_int)

    msg = (
        f"⚠️ **ChatGPT OAuth 재로그인 필요 (D-{days_int})**\n"
        f"refresh token이 약 **{days_int}일 후** 만료됩니다.\n"
        f"만료되면 AI 기능이 중단됩니다.\n\n"
        f"**지금 재로그인하세요:**\n"
        f"```\n"
        f"# 서버(헤드리스)\n"
        f"codex login --device-auth\n\n"
        f"# 로컬\n"
        f"python -m llm.codex_auth --login\n"
        f"```"
    )

    if bot is not None:
        try:
            from notifications.discord_bot import AlertLevel
            await bot.send_alert(msg, AlertLevel.HIGH if days_int > 3 else AlertLevel.URGENT)
            logger.info("refresh token 만료 경고 Discord 발송 완료")
        except Exception as e:
            logger.error("Discord 경고 발송 실패: %s", e)
    else:
        logger.warning("Discord 봇 없음 — 로그로만 경고 출력")


# ──────────────────────────────────────────────────────────────
# 토큰 자동 갱신
# ──────────────────────────────────────────────────────────────

def refresh_access_token() -> Optional[str]:
    """refresh_token으로 새 access_token을 발급받아 auth.json에 저장한다.

    Returns:
        새 access_token 문자열, 실패 시 None
    """
    import requests

    refresh_token = get_refresh_token()
    if not refresh_token:
        logger.error("refresh_token이 없습니다. 재로그인이 필요합니다.")
        return None

    client_id = _get_client_id()
    if not client_id:
        logger.warning(
            "client_id가 auth.json에 없습니다. "
            "Codex CLI 버전에 따라 갱신이 실패할 수 있습니다."
        )

    payload: dict = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if client_id:
        payload["client_id"] = client_id

    logger.info("OAuth 토큰 갱신 시도...")
    try:
        resp = requests.post(
            OAUTH_TOKEN_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        new_access_token  = data.get("access_token")
        new_refresh_token = data.get("refresh_token", refresh_token)  # 없으면 기존 유지
        expires_in        = data.get("expires_in", 86400)             # 기본 24시간

        if not new_access_token:
            logger.error("갱신 응답에 access_token이 없습니다: %s", data)
            return None

        # auth.json 업데이트 (기존 필드 유지하면서 덮어씀)
        auth = _read_auth_file()
        # 키 이름을 auth.json에 이미 있는 형식에 맞춤
        _set_token_fields(auth, new_access_token, new_refresh_token, expires_in)
        _write_auth_file(auth)

        # refresh token이 교체된 경우 발급 시각 갱신
        if new_refresh_token != refresh_token:
            _record_refresh_token_issued()
            logger.info("새 refresh token 발급 — 만료 추적 갱신")

        logger.info("토큰 갱신 성공 (유효시간: %d초)", expires_in)
        return new_access_token

    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        body   = e.response.text[:200] if e.response is not None else ""
        logger.error("토큰 갱신 HTTP 오류 %s: %s", status, body)
        return None
    except Exception as e:
        logger.error("토큰 갱신 실패: %s", e)
        return None


def _set_token_fields(auth: dict, access: str, refresh: str, expires_in: float):
    """auth dict에 토큰 필드를 덮어쓴다. 기존 키 이름을 최대한 유지한다."""
    # access token
    if "accessToken" in auth:
        auth["accessToken"] = access
    elif "access_token" in auth:
        auth["access_token"] = access
    else:
        auth["accessToken"] = access

    # refresh token
    if "refreshToken" in auth:
        auth["refreshToken"] = refresh
    elif "refresh_token" in auth:
        auth["refresh_token"] = refresh
    else:
        auth["refreshToken"] = refresh

    # 만료 시각 (Unix ms — Codex CLI 형식)
    new_expiry_ms = (time.time() + expires_in) * 1000
    if "expiresAt" in auth:
        auth["expiresAt"] = new_expiry_ms
    elif "expires_at" in auth:
        auth["expires_at"] = new_expiry_ms
    else:
        auth["expiresAt"] = new_expiry_ms


# ──────────────────────────────────────────────────────────────
# 유효한 토큰 확보 (갱신 포함)
# ──────────────────────────────────────────────────────────────

def ensure_valid_token() -> Optional[str]:
    """유효한 access_token을 반환한다.

    만료된 경우 refresh_token으로 자동 갱신을 시도한다.
    갱신도 실패하면 None을 반환하고 로그에 재로그인 안내를 남긴다.
    """
    # 1) 만료 여부 확인
    if not is_token_expired():
        token = get_access_token()
        if token:
            return token

    # 2) 만료됨 → refresh 시도
    logger.info("access_token 만료 감지 → refresh_token으로 갱신 시도")
    new_token = refresh_access_token()
    if new_token:
        return new_token

    # 3) refresh 실패 → 재로그인 안내
    logger.error(
        "토큰 자동 갱신 실패. 재로그인이 필요합니다.\n"
        "  서버(headless): codex login --device-auth\n"
        "  로컬:           python -m llm.codex_auth --login"
    )
    return None


# ──────────────────────────────────────────────────────────────
# 헤더 반환 (갱신 포함)
# ──────────────────────────────────────────────────────────────

def get_auth_headers() -> dict:
    """API 호출용 Authorization 헤더를 반환한다.

    필요 시 토큰 자동 갱신을 포함한다.

    Returns:
        {"Authorization": "Bearer <token>", "Content-Type": "application/json"}

    Raises:
        RuntimeError: 유효한 토큰을 확보할 수 없을 때
    """
    token = ensure_valid_token()
    if not token:
        raise RuntimeError(
            "ChatGPT 로그인이 필요합니다.\n"
            "  서버:  codex login --device-auth\n"
            "  로컬:  python -m llm.codex_auth --login"
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ──────────────────────────────────────────────────────────────
# 로그인 상태 확인
# ──────────────────────────────────────────────────────────────

def is_logged_in() -> bool:
    """Codex CLI 로그인 여부를 확인한다 (만료 여부는 무관)."""
    return AUTH_FILE.exists() and bool(get_access_token())


# ──────────────────────────────────────────────────────────────
# Codex CLI 설치 & 로그인
# ──────────────────────────────────────────────────────────────

def _codex_cli_path() -> Optional[str]:
    import shutil
    return shutil.which("codex")


def check_codex_cli() -> bool:
    return _codex_cli_path() is not None


def install_codex_cli() -> bool:
    """npm으로 Codex CLI를 설치한다."""
    import shutil
    if not shutil.which("npm"):
        logger.error("npm이 없습니다. https://nodejs.org 에서 Node.js를 먼저 설치하세요.")
        return False
    logger.info("Codex CLI 설치 중... (npm install -g @openai/codex)")
    try:
        subprocess.run(["npm", "install", "-g", "@openai/codex"], check=True)
        logger.info("Codex CLI 설치 완료")
        return True
    except subprocess.CalledProcessError as e:
        logger.error("설치 실패: %s", e)
        return False


def run_login(force: bool = False) -> bool:
    """브라우저(또는 device-code)로 ChatGPT OAuth 로그인을 실행한다.

    Args:
        force: True면 이미 로그인된 경우에도 재로그인

    Returns:
        로그인 성공 여부
    """
    if not force and is_logged_in() and not is_token_expired():
        logger.info("이미 유효한 로그인 상태입니다.")
        return True

    codex = _codex_cli_path()
    if not codex:
        logger.error("Codex CLI가 설치되어 있지 않습니다.")
        if _ask_yes_no("지금 설치하시겠습니까?"):
            if not install_codex_cli():
                return False
            codex = _codex_cli_path()
        if not codex:
            return False

    # 헤드리스(서버) 환경 감지 → device-auth 사용 안내
    is_headless = not os.environ.get("DISPLAY") and os.name != "nt"
    if is_headless:
        print("\n" + "─" * 55)
        print("  헤드리스(서버) 환경이 감지되었습니다.")
        print("  Device Code 방식으로 로그인합니다.")
        print("  화면에 표시되는 URL과 코드를 다른 기기 브라우저에서 입력하세요.")
        print("─" * 55 + "\n")
        cmd = [codex, "login", "--device-auth"]
    else:
        print("\n" + "─" * 55)
        print("  브라우저가 열립니다.")
        print("  ChatGPT Plus/Pro 계정으로 로그인해주세요.")
        print("─" * 55 + "\n")
        cmd = [codex, "login"]

    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode == 0 and is_logged_in():
            _record_refresh_token_issued()  # 새 로그인 → 발급 시각 기록
            logger.info("로그인 완료. 토큰 저장됨: %s", AUTH_FILE)
            return True
        else:
            logger.warning("로그인이 완료되지 않았습니다. 직접 'codex login'을 실행해보세요.")
            return False
    except FileNotFoundError:
        logger.error("codex 명령어를 찾을 수 없습니다.")
        return False


def _ask_yes_no(question: str) -> bool:
    try:
        return input(f"  {question} (y/N): ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ──────────────────────────────────────────────────────────────
# 상태 출력
# ──────────────────────────────────────────────────────────────

def print_status():
    """현재 로그인 및 토큰 상태를 출력한다."""
    print("\n=== Codex OAuth 상태 ===")

    cli_path = _codex_cli_path()
    print(f"  Codex CLI : {'✔ ' + cli_path if cli_path else '✘ 미설치'}")
    print(f"  auth.json : {'✔ ' + str(AUTH_FILE) if AUTH_FILE.exists() else '✘ 없음'}")

    token = get_access_token()
    if token:
        masked = token[:10] + "..." + token[-6:]
        print(f"  Access    : ✔ {masked}")
    else:
        print("  Access    : ✘ 없음")

    refresh = get_refresh_token()
    print(f"  Refresh   : {'✔ 있음' if refresh else '✘ 없음'}")

    # refresh token 잔여일
    r_days = refresh_token_remaining_days()
    if r_days is None:
        print("  Refresh만료: 추적 정보 없음 (로그인 후 자동 기록됨)")
    elif r_days <= 0:
        print("  Refresh만료: ✘ 만료됨 — 재로그인 필요")
    elif r_days <= REFRESH_WARN_DAYS:
        print(f"  Refresh만료: ⚠ D-{int(r_days)} (곧 만료 — 재로그인 권장)")
    else:
        print(f"  Refresh만료: ✔ {int(r_days)}일 남음")

    remaining = remaining_seconds()
    if remaining is None:
        print("  만료정보  : 알 수 없음 (auth.json에 expiresAt 없음)")
    elif remaining <= 0:
        print("  만료정보  : ✘ 만료됨")
    elif remaining < TOKEN_EXPIRY_BUFFER_SEC * 2:
        print(f"  만료정보  : ⚠ 곧 만료 ({remaining:.0f}초 남음)")
    else:
        h, m = divmod(int(remaining) // 60, 60)
        print(f"  만료정보  : ✔ {h}시간 {m}분 남음")
    print()


# ──────────────────────────────────────────────────────────────
# 직접 실행 진입점
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ChatGPT Codex OAuth 로그인 관리")
    parser.add_argument("--login",   "-l", action="store_true", help="ChatGPT 로그인 (브라우저)")
    parser.add_argument("--status",  "-s", action="store_true", help="로그인 상태 확인")
    parser.add_argument("--refresh", "-r", action="store_true", help="토큰 수동 갱신")
    parser.add_argument("--install",       action="store_true", help="Codex CLI 설치")
    args = parser.parse_args()

    if args.install:
        install_codex_cli()
        return

    if args.refresh:
        print_status()
        result = refresh_access_token()
        if result:
            print("토큰 갱신 성공!")
            print_status()
        else:
            print("갱신 실패. --login 으로 재로그인하세요.")
        return

    if args.status:
        print_status()
        return

    # 기본: 상태 출력 후 필요시 로그인 안내
    print_status()

    if args.login:
        run_login(force=True)
    elif not is_logged_in():
        print("로그인이 필요합니다.")
        if _ask_yes_no("지금 로그인하시겠습니까?"):
            run_login()
    elif is_token_expired():
        print("토큰이 만료되었습니다. 자동 갱신을 시도합니다...")
        if not refresh_access_token():
            print("갱신 실패. 재로그인합니다.")
            run_login(force=True)
    else:
        print("정상 상태입니다. '--login'으로 재로그인, '--refresh'로 수동 갱신할 수 있습니다.")


if __name__ == "__main__":
    main()
