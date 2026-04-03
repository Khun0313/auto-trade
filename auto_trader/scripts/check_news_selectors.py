"""일요일 점검: 뉴스 수집 CSS 셀렉터 정상 동작 확인."""

import sys
import requests
from bs4 import BeautifulSoup

CHECKS = [
    {
        "name": "네이버금융",
        "url": "https://finance.naver.com/news/mainnews.naver",
        "selector": ".articleSubject a",
        "headers": {},
        "fallbacks": ["ul.newsList li a", "dl.mainNewsDl dt a", ".news_list li a"],
    },
    {
        "name": "한국경제",
        "url": "https://www.hankyung.com/economy",
        "selector": ".news-tit a",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "fallbacks": ["h2.news-tit a", "h3.news-tit a", ".article-list a.tit", "h2 a"],
    },
]

MIN_ARTICLES = 10


def check_source(source: dict) -> dict:
    name = source["name"]
    try:
        resp = requests.get(source["url"], headers=source["headers"], timeout=10)
        resp.raise_for_status()
    except Exception as e:
        return {"name": name, "pass": False, "count": 0, "error": str(e), "samples": []}

    soup = BeautifulSoup(resp.text, "lxml")
    articles = soup.select(source["selector"])
    titles = [a.get_text(strip=True) for a in articles if a.get_text(strip=True)]

    result = {
        "name": name,
        "pass": len(titles) >= MIN_ARTICLES,
        "count": len(titles),
        "samples": titles[:3],
        "error": None,
    }

    if not result["pass"]:
        # 대안 셀렉터 탐색
        for sel in source["fallbacks"]:
            found = soup.select(sel)
            found_titles = [a.get_text(strip=True) for a in found if a.get_text(strip=True)]
            if len(found_titles) >= MIN_ARTICLES:
                result["suggestion"] = f"셀렉터 '{sel}' → {len(found_titles)}건 매칭"
                break

    return result


def main():
    print("=== 뉴스 수집기 셀렉터 점검 ===\n")

    all_pass = True
    for source in CHECKS:
        r = check_source(source)
        status = "PASS" if r["pass"] else "FAIL"
        if not r["pass"]:
            all_pass = False

        print(f"[{r['name']}] {status} — {r['count']}건 수집")
        if r["error"]:
            print(f"  오류: {r['error']}")
        for t in r["samples"]:
            print(f"  - {t[:70]}")
        if r.get("suggestion"):
            print(f"  대안: {r['suggestion']}")
        print()

    final = "PASS" if all_pass else "FAIL"
    print(f"최종: {final}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
