"""뉴스 크롤러: 네이버금융, 한경, KRX공시."""

import asyncio
from datetime import datetime
from hashlib import sha256

import aiohttp
from bs4 import BeautifulSoup

from data.db.repository import insert_news
from utils.logger import get_logger

logger = get_logger("news_collector")

NAVER_FINANCE_URL = "https://finance.naver.com/news/mainnews.naver"
HANKYUNG_URL = "https://www.hankyung.com/economy"
KRX_DISCLOSURE_URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"


class NewsCollector:
    """뉴스 수집기."""

    def __init__(self):
        self._seen_hashes: set[str] = set()

    async def collect_all(self) -> list[dict]:
        """모든 소스에서 뉴스를 수집한다."""
        tasks = [
            self._collect_naver(),
            self._collect_hankyung(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_news = []
        for result in results:
            if isinstance(result, list):
                all_news.extend(result)
            elif isinstance(result, Exception):
                logger.error("뉴스 수집 오류: %s", result)

        logger.info("뉴스 수집 완료: %d건", len(all_news))
        return all_news

    async def _collect_naver(self) -> list[dict]:
        """네이버 금융 뉴스를 수집한다."""
        news_list = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(NAVER_FINANCE_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    html = await resp.text()

            soup = BeautifulSoup(html, "lxml")
            articles = soup.select(".articleSubject a")

            for title_tag in articles[:20]:
                title = title_tag.get_text(strip=True)
                if not title:
                    continue

                url = title_tag.get("href", "")
                if url and not url.startswith("http"):
                    url = f"https://finance.naver.com{url}"

                if self._is_duplicate(title):
                    continue

                news = {"title": title, "url": url, "source": "naver_finance"}
                insert_news(title=title, source="naver_finance", url=url)
                news_list.append(news)

        except Exception as e:
            logger.error("네이버 뉴스 수집 오류: %s", e)

        return news_list

    async def _collect_hankyung(self) -> list[dict]:
        """한국경제 뉴스를 수집한다."""
        news_list = []
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession() as session:
                async with session.get(HANKYUNG_URL, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    html = await resp.text()

            soup = BeautifulSoup(html, "lxml")
            articles = soup.select(".news-tit a")

            for article in articles[:20]:
                title = article.get_text(strip=True)
                url = article.get("href", "")

                if not title or self._is_duplicate(title):
                    continue

                news = {"title": title, "url": url, "source": "hankyung"}
                insert_news(title=title, source="hankyung", url=url)
                news_list.append(news)

        except Exception as e:
            logger.error("한경 뉴스 수집 오류: %s", e)

        return news_list

    def _is_duplicate(self, title: str) -> bool:
        """제목 해시로 중복을 확인한다."""
        h = sha256(title.encode()).hexdigest()[:16]
        if h in self._seen_hashes:
            return True
        self._seen_hashes.add(h)
        return False
