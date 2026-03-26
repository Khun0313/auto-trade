"""Streamlit 대시보드: 실시간 모니터링."""

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "db" / "auto_trader.db"


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# === 페이지 설정 ===
st.set_page_config(page_title="Auto Trader", layout="wide")
st.title("국내 주식 자동매매 대시보드")

# === 사이드바 ===
page = st.sidebar.selectbox("페이지", [
    "메인 대시보드", "거래 내역", "전략 모니터", "수익률 차트", "뉴스 피드", "AI 분석 로그", "설정",
])

conn = get_db()


# === 메인 대시보드 ===
if page == "메인 대시보드":
    col1, col2, col3, col4 = st.columns(4)

    # 오늘 거래 통계
    today = date.today().isoformat()
    trades = conn.execute(
        "SELECT * FROM trades WHERE DATE(created_at) = ?", (today,)
    ).fetchall()
    total_pnl = sum(t["pnl"] or 0 for t in trades)
    wins = sum(1 for t in trades if (t["pnl"] or 0) > 0)

    col1.metric("오늘 손익", f"{total_pnl:+,.0f}원")
    col2.metric("거래 수", f"{len(trades)}건")
    col3.metric("승률", f"{wins/len(trades)*100:.0f}%" if trades else "0%")

    # 최근 보고서
    report = conn.execute(
        "SELECT * FROM daily_reports ORDER BY report_date DESC LIMIT 1"
    ).fetchone()
    if report:
        col4.metric("장세", report["market_regime"] or "-")

    # 최근 주문
    st.subheader("최근 주문")
    orders = conn.execute(
        "SELECT * FROM orders ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    if orders:
        df = pd.DataFrame([dict(o) for o in orders])
        st.dataframe(df[["order_id", "stock_code", "stock_name", "side",
                         "order_type", "quantity", "price", "status", "strategy", "created_at"]])

# === 거래 내역 ===
elif page == "거래 내역":
    st.subheader("거래 내역")
    days = st.selectbox("기간", [1, 7, 30], index=1)
    trades = conn.execute(
        "SELECT * FROM trades WHERE created_at >= date('now', ?) ORDER BY created_at DESC",
        (f"-{days} days",)
    ).fetchall()

    if trades:
        df = pd.DataFrame([dict(t) for t in trades])
        st.dataframe(df)

        # 종목별 손익
        if "stock_code" in df.columns and "pnl" in df.columns:
            by_stock = df.groupby("stock_code")["pnl"].sum().reset_index()
            fig = px.bar(by_stock, x="stock_code", y="pnl", title="종목별 손익",
                         color="pnl", color_continuous_scale="RdYlGn")
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("거래 내역이 없습니다.")

# === 전략 모니터 ===
elif page == "전략 모니터":
    st.subheader("전략별 성과")
    evals = conn.execute(
        "SELECT * FROM weekly_evaluations ORDER BY week_start DESC LIMIT 50"
    ).fetchall()

    if evals:
        df = pd.DataFrame([dict(e) for e in evals])
        st.dataframe(df[["strategy", "week_start", "win_rate", "profit_factor",
                         "max_drawdown", "total_trades"]])
    else:
        st.info("평가 데이터가 없습니다.")

# === 수익률 차트 ===
elif page == "수익률 차트":
    st.subheader("누적 수익률")
    reports = conn.execute(
        "SELECT * FROM daily_reports ORDER BY report_date"
    ).fetchall()

    if reports:
        df = pd.DataFrame([dict(r) for r in reports])
        df["cumulative_pnl"] = df["total_pnl"].cumsum()
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["report_date"], y=df["cumulative_pnl"],
                                 mode="lines+markers", name="누적 손익"))
        fig.update_layout(title="누적 손익 추이", xaxis_title="날짜", yaxis_title="원")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("보고서 데이터가 없습니다.")

# === 뉴스 피드 ===
elif page == "뉴스 피드":
    st.subheader("최근 뉴스")
    news = conn.execute(
        "SELECT * FROM news ORDER BY collected_at DESC LIMIT 50"
    ).fetchall()

    for n in news:
        sentiment = n["sentiment_score"]
        color = "green" if sentiment and sentiment > 0.2 else ("red" if sentiment and sentiment < -0.2 else "gray")
        st.markdown(
            f"**{n['title']}** "
            f"<span style='color:{color}'>[{sentiment or '-':.2f}]</span> "
            f"— {n['source']} ({n['collected_at']})",
            unsafe_allow_html=True,
        )

# === AI 분석 로그 ===
elif page == "AI 분석 로그":
    st.subheader("AI 평가 이력")
    reports = conn.execute(
        "SELECT report_date, market_regime, ai_evaluation FROM daily_reports ORDER BY report_date DESC LIMIT 10"
    ).fetchall()

    for r in reports:
        with st.expander(f"{r['report_date']} ({r['market_regime'] or '-'})"):
            st.write(r["ai_evaluation"] or "평가 없음")

# === 설정 ===
elif page == "설정":
    st.subheader("설정")
    st.info("설정 파일 편집은 config/ 디렉토리에서 직접 수행합니다.")

    # 현재 설정 표시
    settings_path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
    if settings_path.exists():
        st.code(settings_path.read_text(encoding="utf-8"), language="yaml")

conn.close()
