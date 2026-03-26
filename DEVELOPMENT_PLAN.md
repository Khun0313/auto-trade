# 국내 주식 자동매매 시스템 — 상세 개발 계획

> **문서 버전**: v1.0
> **작성일**: 2026-03-26
> **기반 문서**: `auto_trader_architecture_v8_final.docx` (v5.0)

## Context

한국투자증권 Open API를 활용한 자동매매 시스템을 처음부터 구축한다.
개발 환경은 Python 3.11+ / Ubuntu 24.04 / SQLite이며, 월 $20(ChatGPT Plus) 외 전부 무료 스택이다.
총 6개 Phase, 약 10주 + 모의투자 4주 + 실전 전환으로 구성한다.

---

## 전체 로드맵 요약

| Phase | 기간 | 핵심 목표 |
|-------|------|-----------|
| 1 | 2주 (Day 1~14) | 기반 구축: 프로젝트 세팅, 인증, 쓰로틀링, 데이터 수집, DB |
| 2 | 2주 (Day 15~28) | 전략 엔진: 장세 판단, 5개 전략, 종목 선정, 리밸런싱 |
| 3 | 1주 (Day 29~35) | 주문 실행 + 리스크 관리 |
| 4 | 1주 (Day 36~42) | 모니터링: 뉴스, AI 연동, 평가, Discord Bot |
| 5 | 2주 (Day 43~56) | 백테스팅 엔진 + 전략 검증 |
| 6-A | 2주+4주 (Day 57~91) | 스케줄러, 대시보드, 모의투자 4주 |
| 6-B | 이후 | 실전 전환 + 자기진화 시스템 |

---

## Phase 1: 기반 구축 (2주)

### Sprint 1-1: 프로젝트 초기 세팅 (Day 1~3)

**목표**: 프로젝트 구조 생성, 의존성 정의, 설정 파일 구조 확립

| # | 작업 | 생성 파일 | 비고 |
|---|------|-----------|------|
| 1 | 프로젝트 디렉토리 구조 생성 | 전체 디렉토리 | `auto_trader/` 루트 |
| 2 | `requirements.txt` 작성 | `requirements.txt` | requests, websockets, apscheduler, pyyaml, discord.py, streamlit, plotly, beautifulsoup4, openai, pandas, numpy, aiohttp 등 |
| 3 | Python 가상환경 설정 스크립트 | `setup.sh` | venv 생성, pip install, DB 초기화, systemd 등록 |
| 4 | `.gitignore` 작성 | `.gitignore` | .env, data/db/, data/logs/, `__pycache__`, *.pyc |
| 5 | `.env.example` 작성 | `.env.example` | API_KEY, API_SECRET, ACCOUNT_NO, DISCORD_TOKEN 등 빈 템플릿 |
| 6 | `settings.yaml` 설정 파일 | `config/settings.yaml` | 환경(모의/실전), 계좌, API URL, 쓰로틀링 설정 |
| 7 | `strategies.yaml` 전략 파라미터 | `config/strategies.yaml` | 각 전략별 초기 파라미터 |
| 8 | `watchlist.yaml` 관심종목 | `config/watchlist.yaml` | ETF 고정 유니버스 + 수동 관심종목 |
| 9 | 로거 유틸리티 | `utils/logger.py` | 일별 파일 회전(30일), 포맷 통일 |
| 10 | systemd 서비스 파일 | `auto-trader.service` | ExecStart, Restart=always, After=network.target |

**검증**: `setup.sh` 실행 → venv 생성 → `pip install -r requirements.txt` 성공 → 로거 동작 확인

---

### Sprint 1-2: API 인증 모듈 (Day 4~6)

**목표**: 한국투자증권 API 토큰 발급/갱신 자동화

| # | 작업 | 생성/수정 파일 | 상세 |
|---|------|---------------|------|
| 1 | 토큰 발급 함수 | `core/auth.py` | POST `/oauth2/tokenP` → access_token 획득 |
| 2 | 토큰 자동 갱신 | `core/auth.py` | 만료 1시간 전 자동 갱신, 24시간 주기 |
| 3 | 토큰 캐싱 | `core/auth.py` | 파일 캐시(`data/token.json`) → 재시작 시 재사용 |
| 4 | 모의/실전 환경 분기 | `core/auth.py` | settings.yaml의 `mode: paper/live`로 URL 자동 전환 |
| 5 | .env 로딩 | `core/auth.py` | `python-dotenv`로 환경변수 로드 |
| 6 | 단위 테스트 | `tests/test_auth.py` | 토큰 발급, 갱신, 캐시 로드 테스트 |

**검증**: 모의투자 계좌로 토큰 발급 → 잔고 조회 API 1회 호출 성공

---

### Sprint 1-3: 쓰로틀링 모듈 (Day 7~8)

**목표**: API 호출 유량 제한 (실전 15건/초, 모의 4건/초)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 슬라이딩 윈도우 쓰로틀러 | `utils/throttle.py` | asyncio 기반, 초당 호출 수 제한 |
| 2 | 데코레이터 방식 적용 | `utils/throttle.py` | `@throttle` 데코레이터로 API 함수에 적용 |
| 3 | 호출 통계 카운터 | `utils/throttle.py` | 분/시간 단위 호출 횟수 로깅 |
| 4 | 단위 테스트 | `tests/test_throttle.py` | 초당 제한 초과 시 대기 동작 확인 |

**검증**: 20건 연속 호출 → 실전 모드에서 15건/초 이하로 제한되는지 확인

---

### Sprint 1-4: 데이터 수집 모듈 (Day 9~14)

**목표**: REST + WebSocket 하이브리드 데이터 수집

#### REST 수집 (Day 9~11)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 분봉 데이터 수집 | `core/data_collector.py` | 5분봉: `/inquire-time-itemchartprice` |
| 2 | 현재가 조회 | `core/data_collector.py` | 1분 주기: `/inquire-price` |
| 3 | 일봉 데이터 수집 | `core/data_collector.py` | 15:40 1회: `/inquire-daily-itemchartprice` |
| 4 | 잔고 조회 | `core/data_collector.py` | 10분 주기: `/inquire-balance` |
| 5 | 투자자별 매매동향 | `core/data_collector.py` | 30분 주기 |

#### WebSocket 수집 (Day 12~13)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 6 | WebSocket 연결 관리 | `core/data_collector.py` | 실시간 체결가(H0STCNT0), 호가(H0STASP0) |
| 7 | 동적 구독 관리 | `core/data_collector.py` | 41종목 한도 내 우선순위 관리 (보유 > 매수후보 > 관심) |
| 8 | 자동 재연결 | `core/data_collector.py` | 연결 끊김 시 지수 백오프 재연결 |

#### DB 구축 (Day 14)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 9 | DB 스키마 생성 | `data/db/schema.sql` | 8개 테이블: stock_prices, orders, trades, signals, news, daily_reports, weekly_evaluations, strategy_params |
| 10 | DB 초기화 모듈 | `data/db/init_db.py` | SQLite DB 파일 생성, 스키마 적용 |
| 11 | 데이터 저장 레이어 | `data/db/repository.py` | INSERT/SELECT 공통 함수 |
| 12 | 통합 테스트 | `tests/test_data_collector.py` | 모의투자 계좌로 분봉 수집 → DB 저장 → 조회 확인 |

**검증**: 모의투자로 삼성전자 5분봉 수집 + WebSocket 실시간 체결가 수신 + DB 저장 확인

---

## Phase 2: 전략 엔진 (2주)

### Sprint 2-1: 장세 판단 + 전략 기반 (Day 15~18)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 장세 판단 모듈 | `analysis/market_regime.py` | KOSPI MA 배열, ADR, 변동폭 → 5단계 분류 (강한 상승/약한 상승/횡보/약한 하락/강한 하락) |
| 2 | BaseStrategy 추상 클래스 | `strategies/base_strategy.py` | `generate_signal()`, `get_confidence()`, `get_parameters()`, `update_parameters()`, `backtest()` |
| 3 | 전략 엔진 | `core/strategy_engine.py` | 전략 로딩, 장세별 활성화, 신호 합산 |
| 4 | 신호 생성기 | `core/signal_generator.py` | `Final_Score = Σ(Signal × Weight × Confidence)`, 임계값 ±0.6 |
| 5 | 단위 테스트 | `tests/test_market_regime.py` | 과거 KOSPI 데이터로 장세 판단 정확도 확인 |

---

### Sprint 2-2: 개별 전략 구현 (Day 19~24)

| # | 전략 | 생성 파일 | 핵심 로직 |
|---|------|-----------|-----------|
| 1 | 변동성 돌파 | `strategies/volatility_breakout.py` | 전일 Range × K배 돌파 시 매수, 당일 종가 매도 |
| 2 | 이동평균선 교차 | `strategies/moving_average.py` | 5일/20일/60일 MA 정배열/역배열 판단 |
| 3 | RSI 역발 | `strategies/rsi_envelope.py` | RSI(14) ≤30 + 볼린저밴드 하단 → 매수 |
| 4 | 엔벨로프 | `strategies/envelope.py` | MA20 ± N% 밴드 터치 후 반등 감지 |
| 5 | 뉴스 감성 | `strategies/news_sentiment.py` | AI 감성점수 기반 + 기술적 확인 |

---

### Sprint 2-3: 종목 선정 + 리밸런싱 (Day 25~28)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 2단계 스크리너 | `screener/stock_screener.py` | 1차(시총/거래량/블랙리스트) → 2차(MA/RSI/수급/ATR) |
| 2 | 활성 감시 목록 관리 | `screener/watchlist_manager.py` | 보유종목(필수) + 고정 ETF + 스크리닝 후보 합산 |
| 3 | 고아 종목 체커 | `screener/orphan_checker.py` | 15:40 일일 체크, 보유기간/전략미적용/수익률정체/최대보유 |
| 4 | 자산 배분기 | `rebalancing/asset_allocator.py` | 장세별 목표 비중, 편차 ±5% 허용, 점진적 50% 이동 |
| 5 | ETF 관리 | `rebalancing/etf_watchlist.py` | 8개 ETF (금/은/채권/달러/원유/KOSPI/인버스) |
| 6 | 통합 테스트 | `tests/test_strategy_engine.py` | 전략→신호→합산 E2E 테스트 |

**검증**: 과거 3개월 데이터로 5개 전략 신호 생성 → 합산 스코어 → 매수/매도 결정 확인

---

## Phase 3: 주문 실행 및 리스크 관리 (1주)

### Sprint 3-1: 리스크 관리 (Day 29~31)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 포지션 사이징 | `core/risk_manager.py` | 단일종목 10%, 거래위험 2%, 일일손실 5%, 최대 10종목, 현금 최소 20% |
| 2 | 손절매/익절매 | `core/risk_manager.py` | 손절 -3%, 추적손절 -2%, 부분익절 +5%, 전량익절 +10%, 시간손절 3일 |
| 3 | 서킷브레이커 | `core/risk_manager.py` | 일일5% / 연속3회손절 / 주간10% / API오류5회 → 자동 정지 |
| 4 | 배당락일 갭 처리 | `core/risk_manager.py` | 배당락일 손절매 기준 조정, 전일 매수 보류 |

---

### Sprint 3-2: 주문 실행 (Day 32~35)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 주문 생성/전송 | `core/order_executor.py` | 확신도별 분기 (>0.8 시장가, 0.6~0.8 지정가 -0.3%) |
| 2 | 체결 확인 | `core/order_executor.py` | WebSocket 체결 수신 + DB 기록 |
| 3 | 미체결 관리 | `core/order_executor.py` | 15:30 장 마감 시 자동 취소 |
| 4 | 중복 주문 방지 | `core/order_executor.py` | 고유 ID + 5분 내 동종 확인 + Lock |
| 5 | 슬리피지 모니터링 | `core/order_executor.py` | 체결가 vs 신호가, 평균 0.5% 초과 시 경고 |
| 6 | 동시호가 처리 | `core/order_executor.py` | 08:30~09:05, 15:20~15:30 주문 차단 |
| 7 | 통합 테스트 | `tests/test_order_executor.py` | 모의투자 매수→체결→매도 E2E |

**검증**: 모의투자에서 소량 매수 → 체결 → 손절매 발동 → 매도 체결 확인

---

## Phase 4: 모니터링 및 평가 (1주)

### Sprint 4-1: 뉴스 수집 + AI 연동 (Day 36~38)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 뉴스 크롤러 | `core/news_collector.py` | 네이버금융, 한경, KRX공시 (BeautifulSoup) |
| 2 | 중복 제거 | `core/news_collector.py` | 제목 해시값 기반 |
| 3 | Codex OAuth 클라이언트 | `llm/codex_client.py` | ChatGPT Codex 5.3 OAuth 토큰 관리 |
| 4 | 감성 분석 파이프라인 | `llm/codex_client.py` | 뉴스 → AI → 감성점수(-1.0~+1.0) + 종목 매핑 |
| 5 | 수집 스케줄 | `core/news_collector.py` | 08:00 / 12:00 / 14:00 3회 |

---

### Sprint 4-2: 일일/주간 평가 (Day 39~40)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 성과 추적기 | `analysis/performance_tracker.py` | 일일 PnL, 실현/미실현 손익, 전략별 기여도 |
| 2 | 일일 보고서 | `llm/daily_evaluator.py` | 15:40 자동, AI 평가 + DB 저장 |
| 3 | 주간 전략 평가 | `analysis/strategy_evaluator.py` | 승률, Sharpe, MDD, Profit Factor |
| 4 | 주간 업그레이드 | `llm/weekly_upgrader.py` | AI 제안 → 백테스트 → 검증 후 적용 |

---

### Sprint 4-3: Discord Bot + 알림 (Day 41~42)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | Bot 기본 구조 | `notifications/discord_bot.py` | discord.py, 채널별 메시지 발송 |
| 2 | 알림 유형 | `notifications/discord_bot.py` | 긴급/높음/보통/낮음 4단계 |
| 3 | 명령어 | `notifications/discord_bot.py` | !status, !balance, !today, !stop, !resume, !report, !ask |
| 4 | AI 질의 연동 | `notifications/discord_bot.py` | !ask → Codex → 응답 |

**검증**: Discord Bot 기동 → !status → 시스템 상태 응답 / 일일 보고서 자동 발송

---

## Phase 5: 백테스트 및 통합 테스트 (2주)

### Sprint 5-1: 백테스팅 엔진 (Day 43~49)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 백테스트 엔진 코어 | `analysis/backtester.py` | 과거 데이터 재생, 전략 시뮬레이션, 거래비용 반영 |
| 2 | 거래비용 모델 | `analysis/backtester.py` | 수수료 0.015% + 세금 0.18% + 슬리피지 0.1~0.3% |
| 3 | Walk-Forward | `analysis/backtester.py` | 80% 학습 + 20% 검증, 롤링 윈도우 |
| 4 | 시장 국면 분류기 | `analysis/market_regime.py` | 8가지 국면 자동 태그 |
| 5 | 과거 데이터 수집 | `scripts/collect_historical.py` | 최소 3년(권장 5년) KOSPI + 개별종목 |
| 6 | 성과 리포트 | `analysis/backtester.py` | Sharpe, MDD, PF, 승률, 국면별 성과 |

---

### Sprint 5-2: 전략 검증 + 통합 (Day 50~56)

| # | 작업 | 상세 |
|---|------|------|
| 1 | 개별 전략 백테스트 | 5개 전략 각각 기준 충족 확인 |
| 2 | 합산 전략 백테스트 | 신호 가중합산 통합 성과 |
| 3 | Out-of-Sample | Train(60%)/Validation(20%)/Test(20%) 분리 |
| 4 | 리밸런싱 백테스트 | ETF 자산배분 시뮬레이션 |
| 5 | 기준 미달 조정 | 파라미터 튜닝 후 재검증 |
| 6 | 통합 E2E 테스트 | 전체 파이프라인 |

**백테스트 통과 기준**:

| 지표 | 최소 | 권장 |
|------|------|------|
| Sharpe Ratio | > 1.0 | > 1.5 |
| 최대 드로다운 | < 20% | < 10% |
| Profit Factor | > 1.2 | > 1.5 |
| 승률 | > 50% | > 55% |
| OOS 성과 | ≥ IS의 70% | ≥ IS의 85% |

---

## Phase 6-A: 스케줄러 + 대시보드 + 모의투자

### Sprint 6A-1: 스케줄러 + 메인 진입점 (Day 57~59)

| # | 작업 | 생성 파일 | 상세 |
|---|------|-----------|------|
| 1 | 스케줄 관리자 | `core/scheduler.py` | APScheduler 기반 일일 타임테이블 |
| 2 | 휴장일 관리 | `core/scheduler.py` | KRX API/JSON 캘린더 |
| 3 | 메인 진입점 | `main.py` | 기동 → 토큰 → 스케줄 → 이벤트 루프 |

**일일 스케줄**:

```
07:30  시스템 기동, 토큰 갱신, 휴장일 확인
08:00  아침 뉴스 수집 + 감성 분석
08:30  종목 스크리닝 (2단계 필터링)
08:50  장세 판단, 전략 활성화, 웹소켓 연결
09:05  매매 시작 (동시호가 회피)
09:05~15:20  매 5분 분봉 → 신호 → 주문 (반복)
12:00  점심 뉴스 수집
14:00  오후 뉴스 수집
15:20  신규 주문 중지
15:30  미체결 주문 취소
15:40  일봉 수집, 고아 체크, 일일 보고서, Discord 발송
16:00 (금)  주간 전략 평가 + AI 업그레이드
16:30 (금)  자산 리밸런싱
18:00  시스템 종료
```

---

### Sprint 6A-2: Streamlit 대시보드 (Day 60~63)

| # | 페이지 | 표시 내용 | 갱신 주기 |
|---|--------|-----------|-----------|
| 1 | 메인 대시보드 | 총자산, 수익률, 전략 상태, 보유종목 | 실시간 (5초) |
| 2 | 거래 내역 | 일/주/월 거래 테이블, 종목별 손익 | 실시간 |
| 3 | 전략 모니터 | 전략별 승률/수익률 차트 | 매 5분 |
| 4 | 수익률 차트 | 누적 수익률, 드로다운, KOSPI 대비 | 매일 |
| 5 | 뉴스 피드 | 뉴스 + 감성점수 | 수집 시 |
| 6 | AI 분석 로그 | 일일/주간 AI 평가 이력 | 일일/주간 |
| 7 | 설정 페이지 | 관심종목, 전략 파라미터 | 수동 |

---

### Sprint 6A-3: 모의투자 운영 (Day 64~91, 4주)

| 주차 | 초점 | 체크 항목 |
|------|------|-----------|
| 1주차 | 안정성 | API 오류율, 재연결, 토큰 갱신, 휴장일 |
| 2주차 | 전략 성과 | 신호 정확도, 슬리피지, 체결률 vs 백테스트 |
| 3주차 | 리스크 | 손절매, 서킷브레이커, 포지션 사이징 |
| 4주차 | 종합 | 보고서 품질, 리밸런싱, 고아 종목 체크 |

**모의투자 통과 기준**:
- 시스템 비정상 종료 0회
- 백테스트 대비 성과 괴리 30% 이내
- 중복 주문 0건
- 손절매/익절매 정상 작동 100%

---

## Phase 6-B: 실전 전환 및 확장

### 실전 전환 체크리스트

- [ ] `settings.yaml` 모드 전환: `mode: paper` → `mode: live`
- [ ] 실전 API 키 `.env` 설정
- [ ] 쓰로틀링 초당 15건 확인
- [ ] 소규모 자본으로 시작
- [ ] Discord `!stop` 명령 동작 확인
- [ ] 수동 개입 금지 규칙 문서화

### 자기진화 로드맵 (HyperAgent 개념)

| 시기 | 단계 | 진화 대상 | 위험도 |
|------|------|-----------|--------|
| 1~2개월 | 1단계 | 전략 파라미터, 신호 합산 임계값 | 낮음 |
| 3~4개월 | 2단계 | 전략 가중치, 스크리닝 기준 | 중간 |
| 5~6개월 | 3단계 | 손절매/익절매 기준 | 높음 |
| 6개월+ | 4단계 | 리밸런싱 비중, 평가 프레임워크 | 매우 높음 |

---

## 전체 파일 목록

```
auto_trader/
├── config/
│   ├── settings.yaml          # 전체 설정
│   ├── strategies.yaml        # 전략 파라미터
│   └── watchlist.yaml         # 관심종목
├── core/
│   ├── auth.py                # API 인증/토큰
│   ├── data_collector.py      # 시세 데이터 수집
│   ├── news_collector.py      # 뉴스 수집
│   ├── strategy_engine.py     # 전략 엔진
│   ├── signal_generator.py    # 신호 생성
│   ├── risk_manager.py        # 리스크 관리
│   ├── order_executor.py      # 주문 실행
│   └── scheduler.py           # 스케줄 관리
├── screener/
│   ├── stock_screener.py      # 2단계 스크리너
│   ├── watchlist_manager.py   # 감시 목록 관리
│   └── orphan_checker.py      # 고아 종목 방지
├── strategies/
│   ├── base_strategy.py       # 전략 추상 클래스
│   ├── volatility_breakout.py # 변동성 돌파
│   ├── moving_average.py      # 이동평균선
│   ├── rsi_envelope.py        # RSI + 볼린저
│   ├── envelope.py            # 엔벨로프
│   ├── news_sentiment.py      # 뉴스 감성
│   └── rebalancer.py          # 리밸런싱 전략
├── rebalancing/
│   ├── asset_allocator.py     # 자산 배분
│   └── etf_watchlist.py       # ETF 종목 관리
├── analysis/
│   ├── market_regime.py       # 장세 판단
│   ├── performance_tracker.py # 성과 추적
│   ├── strategy_evaluator.py  # 전략 평가
│   └── backtester.py          # 백테스팅
├── data/
│   ├── db/
│   │   ├── schema.sql         # DB 스키마
│   │   ├── init_db.py         # DB 초기화
│   │   └── repository.py      # 데이터 접근 레이어
│   ├── logs/                  # 일별 로그
│   └── reports/               # 보고서
├── notifications/
│   └── discord_bot.py         # Discord Bot
├── llm/
│   ├── codex_client.py        # ChatGPT Codex 클라이언트
│   ├── daily_evaluator.py     # 일일 AI 평가
│   └── weekly_upgrader.py     # 주간 AI 업그레이드
├── dashboard/
│   └── app.py                 # Streamlit 대시보드
├── scripts/
│   └── collect_historical.py  # 과거 데이터 수집
├── tests/
│   ├── test_auth.py
│   ├── test_throttle.py
│   ├── test_data_collector.py
│   ├── test_strategy_engine.py
│   ├── test_order_executor.py
│   └── test_market_regime.py
├── utils/
│   ├── logger.py              # 로깅
│   └── throttle.py            # API 유량 제한
├── main.py                    # 메인 진입점
├── requirements.txt           # 의존성
├── setup.sh                   # 초기 설치 스크립트
├── auto-trader.service        # systemd 서비스
├── .gitignore
└── .env.example               # 환경변수 템플릿
```
