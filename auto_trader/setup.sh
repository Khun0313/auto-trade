#!/bin/bash
set -e

# 색상 정의
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       Auto Trader 초기 설정              ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

TOTAL_STEPS=7

# ─────────────────────────────────────────────
# [1/7] Python 가상환경
# ─────────────────────────────────────────────
echo -e "${BLUE}[1/${TOTAL_STEPS}] Python 가상환경 설정...${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo -e "  ${GREEN}✔ 가상환경 생성 완료${NC}"
else
    echo -e "  ${YELLOW}→ 가상환경이 이미 존재합니다${NC}"
fi

source .venv/bin/activate

# ─────────────────────────────────────────────
# [2/7] Python 패키지 설치
# ─────────────────────────────────────────────
echo -e "${BLUE}[2/${TOTAL_STEPS}] Python 패키지 설치...${NC}"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "  ${GREEN}✔ 패키지 설치 완료${NC}"

# ─────────────────────────────────────────────
# [3/7] 데이터 디렉토리 생성
# ─────────────────────────────────────────────
echo -e "${BLUE}[3/${TOTAL_STEPS}] 데이터 디렉토리 생성...${NC}"
mkdir -p data/db data/logs data/reports
echo -e "  ${GREEN}✔ 디렉토리 생성 완료${NC}"

# ─────────────────────────────────────────────
# [4/7] DB 초기화
# ─────────────────────────────────────────────
echo -e "${BLUE}[4/${TOTAL_STEPS}] DB 초기화...${NC}"
python -c "from data.db.init_db import init_database; init_database()"
echo -e "  ${GREEN}✔ DB 초기화 완료${NC}"

# ─────────────────────────────────────────────
# [5/7] .env 파일 설정
# ─────────────────────────────────────────────
echo -e "${BLUE}[5/${TOTAL_STEPS}] 환경 변수 파일 설정...${NC}"
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "  ${YELLOW}→ .env 파일을 생성했습니다. API 키를 입력해주세요${NC}"
else
    echo -e "  ${YELLOW}→ .env 파일이 이미 존재합니다${NC}"
fi

# ─────────────────────────────────────────────
# [6/7] Node.js & Codex CLI 설치
# ─────────────────────────────────────────────
echo -e "${BLUE}[6/${TOTAL_STEPS}] Codex CLI 설치...${NC}"

# Node.js 확인
if ! command -v node &> /dev/null; then
    echo -e "  ${RED}✘ Node.js가 설치되어 있지 않습니다.${NC}"
    echo ""
    echo -e "  Node.js 설치 방법:"
    echo -e "  ${YELLOW}Ubuntu/Debian:${NC}"
    echo -e "    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -"
    echo -e "    sudo apt-get install -y nodejs"
    echo -e "  ${YELLOW}Windows:${NC}"
    echo -e "    https://nodejs.org 에서 다운로드"
    echo ""
    echo -e "  ${YELLOW}→ Node.js 설치 후 setup.sh를 다시 실행하세요${NC}"
    echo -e "  ${YELLOW}→ 지금은 나머지 설정을 계속 진행합니다...${NC}"
    CODEX_INSTALLED=false
else
    NODE_VERSION=$(node --version)
    echo -e "  Node.js ${NODE_VERSION} 확인됨"

    # npm 확인
    if ! command -v npm &> /dev/null; then
        echo -e "  ${RED}✘ npm이 없습니다. Node.js를 재설치해주세요.${NC}"
        CODEX_INSTALLED=false
    else
        # npm 전역 설치 경로를 홈 디렉토리로 설정 (sudo 불필요)
        NPM_GLOBAL_DIR="$HOME/.npm-global"
        if [ "$(npm prefix -g 2>/dev/null)" != "$NPM_GLOBAL_DIR" ]; then
            echo -e "  npm 전역 경로 설정 중... ($NPM_GLOBAL_DIR)"
            mkdir -p "$NPM_GLOBAL_DIR"
            npm config set prefix "$NPM_GLOBAL_DIR"
        fi
        export PATH="$NPM_GLOBAL_DIR/bin:$PATH"

        # ~/.bashrc에 PATH 영구 등록 (없을 때만)
        BASHRC="$HOME/.bashrc"
        if ! grep -q "npm-global/bin" "$BASHRC" 2>/dev/null; then
            echo "" >> "$BASHRC"
            echo "# npm 전역 패키지 경로" >> "$BASHRC"
            echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$BASHRC"
            echo -e "  ${YELLOW}→ ~/.bashrc에 PATH 등록 완료 (다음 로그인부터 영구 적용)${NC}"
        fi

        # Codex CLI 설치 여부 확인
        if command -v codex &> /dev/null; then
            CODEX_VERSION=$(codex --version 2>/dev/null || echo "설치됨")
            echo -e "  ${YELLOW}→ Codex CLI가 이미 설치되어 있습니다 (${CODEX_VERSION})${NC}"
            CODEX_INSTALLED=true
        else
            echo -e "  Codex CLI를 설치합니다..."
            npm install -g @openai/codex
            INSTALL_EXIT=$?

            if [ $INSTALL_EXIT -eq 0 ] && command -v codex &> /dev/null; then
                echo -e "  ${GREEN}✔ Codex CLI 설치 완료${NC}"
                CODEX_INSTALLED=true
            else
                echo -e "  ${RED}✘ Codex CLI 설치 실패.${NC}"
                echo -e "    npm 로그 확인: ~/.npm/_logs/"
                CODEX_INSTALLED=false
            fi
        fi
    fi
fi

# ─────────────────────────────────────────────
# [7/7] ChatGPT OAuth 로그인
# ─────────────────────────────────────────────
echo -e "${BLUE}[7/${TOTAL_STEPS}] ChatGPT OAuth 로그인...${NC}"

AUTH_FILE="$HOME/.codex/auth.json"

if [ "$CODEX_INSTALLED" = false ]; then
    echo -e "  ${YELLOW}→ Codex CLI 미설치로 로그인을 건너뜁니다${NC}"
    echo -e "  ${YELLOW}→ 설치 후 'codex login' 또는 'python -m llm.codex_auth' 를 실행하세요${NC}"
elif [ -f "$AUTH_FILE" ]; then
    echo -e "  ${YELLOW}→ 이미 로그인되어 있습니다${NC}"
    echo -e "    토큰 파일: ${AUTH_FILE}"
    echo ""
    read -r -p "  재로그인 하시겠습니까? (y/N): " RELOGIN
    if [[ "$RELOGIN" =~ ^[Yy]$ ]]; then
        echo -e "  브라우저가 열립니다. ChatGPT 계정으로 로그인해주세요..."
        codex login
        echo -e "  ${GREEN}✔ 로그인 완료${NC}"
    else
        echo -e "  ${YELLOW}→ 기존 로그인 상태를 유지합니다${NC}"
    fi
else
    echo ""
    echo -e "  ${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  브라우저가 열립니다."
    echo -e "  ChatGPT Plus/Pro 계정으로 로그인해주세요."
    echo -e "  ${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    codex login

    if [ -f "$AUTH_FILE" ]; then
        echo -e "  ${GREEN}✔ 로그인 완료 (토큰 저장됨: ${AUTH_FILE})${NC}"
    else
        echo -e "  ${RED}✘ 로그인이 완료되지 않은 것 같습니다.${NC}"
        echo -e "  수동으로 'codex login' 을 실행해주세요."
    fi
fi

# ─────────────────────────────────────────────
# 완료 메시지
# ─────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           설정 완료!                     ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "다음 단계:"
echo -e "  1. ${YELLOW}.env${NC} 파일에 한투증권 API 키 / Discord 토큰 확인"
echo -e "  2. ${YELLOW}codex login${NC} 으로 ChatGPT 로그인 (미완료 시)"
echo -e "  3. ${YELLOW}python main.py${NC} 로 시스템 시작"
echo ""
