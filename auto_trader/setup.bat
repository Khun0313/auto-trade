@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

echo.
echo ╔══════════════════════════════════════════╗
echo ║       Auto Trader 초기 설정 (Windows)   ║
echo ╚══════════════════════════════════════════╝
echo.

set TOTAL=7
set STEP=0

:: ─────────────────────────────────────────────
:: [1/7] Python 가상환경
:: ─────────────────────────────────────────────
set /a STEP+=1
echo [%STEP%/%TOTAL%] Python 가상환경 설정...
if not exist ".venv" (
    python -m venv .venv
    echo   [OK] 가상환경 생성 완료
) else (
    echo   [--] 가상환경이 이미 존재합니다
)
call .venv\Scripts\activate.bat

:: ─────────────────────────────────────────────
:: [2/7] Python 패키지 설치
:: ─────────────────────────────────────────────
set /a STEP+=1
echo [%STEP%/%TOTAL%] Python 패키지 설치...
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo   [OK] 패키지 설치 완료

:: ─────────────────────────────────────────────
:: [3/7] 데이터 디렉토리
:: ─────────────────────────────────────────────
set /a STEP+=1
echo [%STEP%/%TOTAL%] 데이터 디렉토리 생성...
if not exist "data\db" mkdir data\db
if not exist "data\logs" mkdir data\logs
if not exist "data\reports" mkdir data\reports
echo   [OK] 디렉토리 생성 완료

:: ─────────────────────────────────────────────
:: [4/7] DB 초기화
:: ─────────────────────────────────────────────
set /a STEP+=1
echo [%STEP%/%TOTAL%] DB 초기화...
python -c "from data.db.init_db import init_database; init_database()"
echo   [OK] DB 초기화 완료

:: ─────────────────────────────────────────────
:: [5/7] .env 파일
:: ─────────────────────────────────────────────
set /a STEP+=1
echo [%STEP%/%TOTAL%] 환경 변수 파일 설정...
if not exist ".env" (
    copy .env.example .env > nul
    echo   [--] .env 파일 생성됨. API 키를 입력해주세요.
) else (
    echo   [--] .env 파일이 이미 존재합니다
)

:: ─────────────────────────────────────────────
:: [6/7] Node.js & Codex CLI 설치
:: ─────────────────────────────────────────────
set /a STEP+=1
echo [%STEP%/%TOTAL%] Codex CLI 설치...
set CODEX_OK=0

where node > nul 2>&1
if errorlevel 1 (
    echo   [!!] Node.js가 설치되어 있지 않습니다.
    echo        https://nodejs.org 에서 Node.js LTS를 설치해주세요.
    echo        설치 후 setup.bat를 다시 실행하세요.
    goto :SKIP_CODEX
)

for /f "tokens=*" %%v in ('node --version 2^>nul') do set NODE_VER=%%v
echo   Node.js %NODE_VER% 확인됨

where codex > nul 2>&1
if not errorlevel 1 (
    echo   [--] Codex CLI가 이미 설치되어 있습니다
    set CODEX_OK=1
    goto :DO_LOGIN
)

echo   Codex CLI를 설치합니다...
npm install -g @openai/codex
if errorlevel 1 (
    echo   [!!] 설치 실패. 관리자 권한으로 cmd를 열고 다시 시도해보세요.
    goto :SKIP_CODEX
)
echo   [OK] Codex CLI 설치 완료
set CODEX_OK=1

:: ─────────────────────────────────────────────
:: [7/7] ChatGPT OAuth 로그인
:: ─────────────────────────────────────────────
:DO_LOGIN
set /a STEP+=1
echo [%STEP%/%TOTAL%] ChatGPT OAuth 로그인...

set AUTH_FILE=%USERPROFILE%\.codex\auth.json
if exist "%AUTH_FILE%" (
    echo   [--] 이미 로그인되어 있습니다
    echo        토큰 파일: %AUTH_FILE%
    set /p RELOGIN="   재로그인 하시겠습니까? (y/N): "
    if /i "!RELOGIN!"=="y" (
        echo   브라우저가 열립니다. ChatGPT 계정으로 로그인해주세요...
        codex login
        echo   [OK] 로그인 완료
    )
    goto :DONE
)

echo.
echo   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo   브라우저가 열립니다.
echo   ChatGPT Plus/Pro 계정으로 로그인해주세요.
echo   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
codex login

if exist "%AUTH_FILE%" (
    echo   [OK] 로그인 완료 (토큰 저장됨)
) else (
    echo   [!!] 로그인이 완료되지 않은 것 같습니다.
    echo        직접 'codex login' 을 실행해보세요.
)
goto :DONE

:SKIP_CODEX
set /a STEP+=1
echo [%STEP%/%TOTAL%] ChatGPT OAuth 로그인... (Codex CLI 미설치로 건너뜀)

:DONE
echo.
echo ╔══════════════════════════════════════════╗
echo ║           설정 완료!                     ║
echo ╚══════════════════════════════════════════╝
echo.
echo 다음 단계:
echo   1. .env 파일에 한투증권 API 키 / Discord 토큰 확인
echo   2. codex login 으로 ChatGPT 로그인 (미완료 시)
echo   3. python main.py 로 시스템 시작
echo.
pause
