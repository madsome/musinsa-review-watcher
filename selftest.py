#!/usr/bin/env python3
"""
네트워크 없이 watcher 의 판별·조립 로직을 검증한다.

무신사 API 를 흉내 낸 가짜 응답을 fetch_page 자리에 끼워 넣고,
신규 판별 / 페이지 넘김 / 상태 저장 / 메일 본문 조립이 의도대로 도는지 확인한다.

    python selftest.py
"""

import json
import sys
from pathlib import Path

import watcher

ROOT = Path(__file__).resolve().parent
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))


def make_review(no: int, grade: int = 5, content: str = "핏이 좋아요",
                created: str = "2026-08-05T12:00:00.000+09:00", option: str = "01.블랙 · 32"):
    """실제 API 응답과 같은 형태의 후기 한 건."""
    return {
        "no": no,
        "typeName": "일반",
        "content": content,
        "grade": str(grade),
        "createDate": created,
        "likeCount": 0,
        "goodsOption": option,
        "images": None,
        "userProfileInfo": {"userNickName": "tester", "userHeight": 175, "userWeight": 70},
    }


def fake_api(pages_by_goods):
    """goodsNo -> [page0 리뷰목록, page1 리뷰목록, ...] 를 받아 fetch_page 대체 함수를 만든다."""
    calls = []

    def _fetch(goods_no, page, session):
        calls.append((goods_no, page))
        pages = pages_by_goods[goods_no]
        items = pages[page] if page < len(pages) else []
        return {"list": items, "total": sum(len(p) for p in pages)}

    return _fetch, calls


def run():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    black = cfg["products"][0]
    orig = watcher.fetch_page

    print("\n[1] 정규화")
    row = watcher.normalize(
        make_review(100, 4, "좋아요\n\n  냄새가  좀 나요", option="01.블랙 · 32"), "블랙")
    check("줄바꿈·연속공백이 한 칸으로 정리됨", row["content"] == "좋아요 냄새가 좀 나요", row["content"])
    check("사이즈 추출", row["size"] == "32", row["size"])
    check("평점 int 변환", row["grade"] == 4)
    check("작성일시 포맷", row["created_at"] == "2026-08-05 12:00:00", row["created_at"])

    print("\n[2] 신규 판별 (high-water mark)")
    watcher.fetch_page, _ = fake_api({4780658: [[make_review(n) for n in (105, 104, 103, 102, 101)]]})
    items, total = watcher.collect_new(black, last_no=103, max_pages=10, since=None, session=None)
    check("103 초과분만 신규", [i["no"] for i in items] == [105, 104], str([i["no"] for i in items]))
    check("누적 건수 전달", total == 5)

    print("\n[3] 이미 다 본 상태면 신규 0건")
    items, _ = watcher.collect_new(black, last_no=105, max_pages=10, since=None, session=None)
    check("신규 없음", items == [])

    print("\n[4] 최초 실행(last_no=0)이어도 폭주하지 않음 — 페이지 1장만 요청")
    full = [make_review(n) for n in range(220, 200, -1)]          # 20건 = 한 페이지 가득
    watcher.fetch_page, calls = fake_api({4780658: [full, [make_review(n) for n in range(200, 180, -1)]]})
    items, _ = watcher.collect_new(black, last_no=0, max_pages=1, since=None, session=None)
    check("page 0 만 조회", calls == [(4780658, 0)], str(calls))
    check("20건 수집", len(items) == 20)

    print("\n[5] 신규가 한 페이지를 넘으면 다음 페이지까지 이어서 조회")
    watcher.fetch_page, calls = fake_api({4780658: [full, [make_review(n) for n in range(200, 180, -1)]]})
    items, _ = watcher.collect_new(black, last_no=195, max_pages=10, since=None, session=None)
    check("2페이지까지 조회", calls == [(4780658, 0), (4780658, 1)], str(calls))
    check("196~220 총 25건 수집", len(items) == 25, str(len(items)))
    check("경계값 195는 제외", 195 not in [i["no"] for i in items])

    print("\n[6] --since-days 모드 (날짜 기준)")
    from datetime import datetime, timedelta
    now = datetime.now(watcher.KST)
    recent = (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.000+09:00")
    old = (now - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%S.000+09:00")
    watcher.fetch_page, _ = fake_api({4780658: [[make_review(300, created=recent),
                                                 make_review(299, created=old)]]})
    items, _ = watcher.collect_new(black, last_no=999999, max_pages=10,
                                   since=now - timedelta(days=3), session=None)
    check("기간 내 1건만", [i["no"] for i in items] == [300], str([i["no"] for i in items]))

    print("\n[7] 메일 본문 조립")
    groups = {
        "블랙": [watcher.normalize(make_review(400, 2, "냄새가 너무 심해서 못 입겠어요"), "블랙")],
        "그레이": [watcher.normalize(make_review(401, 5, "시원하고 핏 좋아요", option="03.그레이 · 30"), "그레이")],
    }
    totals = {"블랙": 384, "그레이": 917}
    subject = watcher.build_subject(groups)
    text = watcher.build_text(groups, totals)
    html_body = watcher.build_html(groups, totals, cfg)

    check("제목에 건수", "새 후기 2건" in subject, subject)
    check("제목에 색상별 내역", "블랙 1건" in subject and "그레이 1건" in subject, subject)
    check("저평점 경고 표시", "저평점 포함" in subject, subject)
    check("본문에 후기 전문 포함", "냄새가 너무 심해서 못 입겠어요" in text)
    check("HTML 에 주의 키워드 표시", "주의 키워드" in html_body and "냄새" in html_body)
    check("HTML 에 누적 건수", "917" in html_body)
    check("HTML 이스케이프 동작", "&lt;" in watcher.build_html(
        {"블랙": [watcher.normalize(make_review(1, 5, "<script>x</script>"), "블랙")]}, totals, cfg))
    check("평균 평점 계산", "2.00" in html_body and "5.00" in html_body)

    print("\n[8] 별점 표기")
    check("5점", watcher.stars(5) == "★★★★★")
    check("2점", watcher.stars(2) == "★★☆☆☆")
    check("무평점(0)", watcher.stars(0) == "무평점")

    watcher.fetch_page = orig

    print("\n" + "=" * 52)
    print(f"통과 {len(PASS)}건 / 실패 {len(FAIL)}건")
    if FAIL:
        for f in FAIL:
            print("  실패:", f)
        return 1
    print("모든 검사 통과")
    return 0


if __name__ == "__main__":
    sys.exit(run())
