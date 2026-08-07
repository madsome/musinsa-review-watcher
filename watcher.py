#!/usr/bin/env python3
"""
무신사 상품 후기 감시기 (Musinsa Review Watcher)

지정한 상품의 새 후기를 주기적으로 확인해 이메일로 알려준다.

동작 방식
---------
후기 번호(`no`)는 시간이 지날수록 커지므로, 상품별로 마지막에 본 최대 번호를
state.json 에 기록해 두고(high-water mark) 그보다 큰 번호만 신규로 판정한다.
실행 주기가 밀리거나 건너뛰어도 누락·중복이 생기지 않는다.

사용법
------
    python watcher.py                 # 신규 후기 확인 후 있으면 메일 발송
    python watcher.py --init          # 알림 없이 현재 시점을 기준선으로 저장 (최초 1회)
    python watcher.py --dry-run       # 메일을 보내지 않고 결과만 출력
    python watcher.py --test-email    # 설정 확인용 테스트 메일 발송
    python watcher.py --since-days 3  # 최근 3일치를 강제로 신규 취급 (재발송/점검용)

환경 변수 (GitHub Actions 에서는 Secrets 로 주입)
--------------------------------------------
    GMAIL_USER            보내는 Gmail 주소          (예: thisman01@gmail.com)
    GMAIL_APP_PASSWORD    Gmail 앱 비밀번호 16자리   (계정 비밀번호 아님)
    MAIL_TO               받는 주소. 없으면 GMAIL_USER 로 보냄. 쉼표로 여러 개 가능
"""

from __future__ import annotations

import argparse
import html
import json
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"

API_URL = "https://goods.musinsa.com/api2/review/v1/view/list"
PAGE_SIZE = 20                       # 무신사 API 상한. 21 이상은 400 으로 거부됨
KST = timezone(timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# --------------------------------------------------------------------------- #
# 설정 / 상태
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        with STATE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("[warn] state.json 을 읽지 못해 빈 상태로 시작합니다.", file=sys.stderr)
        return {}


def save_state(state: dict) -> None:
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------- #
# 무신사 API
# --------------------------------------------------------------------------- #

def product_key(product: dict) -> str:
    """상태·그룹을 구분하는 키.

    한 상품번호 안에 여러 색상이 옵션으로 묶인 상품이 있어서(예: 커브드 데님 팬츠 8 colors)
    상품번호만으로는 색상끼리 구분되지 않는다. option1 이 있으면 키에 함께 넣는다.
    """
    opt = product.get("option1")
    return f'{product["goods_no"]}|{opt}' if opt else str(product["goods_no"])


def product_url(product: dict) -> str:
    base = f'https://www.musinsa.com/review/goods/{product["goods_no"]}?sort=new'
    opt = product.get("option1")
    return base + (f"&option1List%5B0%5D={quote(opt)}" if opt else "")


def fetch_page(goods_no: int, page: int, session: requests.Session, option1=None) -> dict:
    """후기 목록 한 페이지를 가져온다.

    selectedSimilarNo 를 goodsNo 와 같게 넣어야 해당 색상만 걸러진다.
    빼면 같은 시리즈의 다른 색상 후기까지 섞여 나온다.

    option1 은 한 상품번호 안에서 색상이 옵션으로 갈리는 상품에만 쓴다.
    무신사 후기 페이지의 색상 필터와 같은 값이며 번호 접두사까지 정확히 맞아야 한다
    (예: "01.블랙"). 이 경우 selectedSimilarNo 는 넣지 않는다.
    """
    params = {
        "page": page,
        "pageSize": PAGE_SIZE,
        "goodsNo": goods_no,
        "sort": "new",
    }
    if option1:
        params["option1List"] = option1
    else:
        params["selectedSimilarNo"] = goods_no
    url = f"{API_URL}?{urlencode(params)}"

    last_err = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=20,
                            headers={"User-Agent": UA, "Accept": "application/json"})
            r.raise_for_status()
            body = r.json()
            if body.get("meta", {}).get("result") != "SUCCESS" or body.get("data") is None:
                raise RuntimeError(f"API 오류 응답: {body.get('meta')}")
            return body["data"]
        except Exception as e:                       # noqa: BLE001
            last_err = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    tag = f"goodsNo={goods_no}" + (f" option1={option1}" if option1 else "")
    raise RuntimeError(f"{tag} page={page} 조회 실패: {last_err}")


def normalize(item: dict, label: str) -> dict:
    profile = item.get("userProfileInfo") or {}
    option = (item.get("goodsOption") or "").split("·")
    size = option[-1].strip() if len(option) > 1 else ""
    return {
        "no": item["no"],
        "color": label,
        "grade": int(item.get("grade") or 0),
        "created_at": (item.get("createDate") or "")[:19].replace("T", " "),
        "likes": item.get("likeCount") or 0,
        "size": size,
        "content": " ".join((item.get("content") or "").split()),
        "nickname": profile.get("userNickName") or "",
        "height": profile.get("userHeight"),
        "weight": profile.get("userWeight"),
        "photos": len(item.get("images") or []),
        "kind": item.get("typeName") or "",          # 일반 / 체험단
    }


def collect_new(product: dict, last_no: int, max_pages: int,
                since, session: requests.Session):
    """last_no 보다 큰 번호의 후기를 모두 모은다. (신규 목록, 전체 후기 수) 반환."""
    goods_no = product["goods_no"]
    label = product["label"]
    option1 = product.get("option1")
    new_items = []
    total = 0

    for page in range(max_pages):
        data = fetch_page(goods_no, page, session, option1)
        total = data.get("total", total)
        items = data.get("list") or []
        if not items:
            break

        stop = False
        for raw in items:
            row = normalize(raw, label)
            if since is not None:
                # --since-days 모드: 날짜 기준으로만 판정
                try:
                    created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                except ValueError:
                    continue
                if created < since:
                    stop = True
                    break
                new_items.append(row)
            else:
                if row["no"] <= last_no:
                    stop = True
                    break
                new_items.append(row)

        if stop or len(items) < PAGE_SIZE:
            break
        time.sleep(0.3)                              # 서버 배려

    return new_items, total


# --------------------------------------------------------------------------- #
# 메일 본문
# --------------------------------------------------------------------------- #

def stars(grade: int) -> str:
    return "무평점" if grade <= 0 else "★" * grade + "☆" * (5 - grade)


def build_subject(groups) -> str:
    """제목이 너무 길어지지 않도록 상품이 많으면 앞 3개만 쓰고 나머지는 묶는다."""
    total = sum(len(g["items"]) for g in groups.values())
    hit = [(g["label"], len(g["items"])) for g in groups.values() if g["items"]]
    hit.sort(key=lambda x: -x[1])

    if len(hit) <= 3:
        detail = ", ".join(f"{lab} {n}건" for lab, n in hit)
    else:
        head = ", ".join(f"{lab} {n}건" for lab, n in hit[:3])
        detail = f"{head} 외 {len(hit) - 3}개 상품"

    low = sum(1 for g in groups.values() for r in g["items"] if 0 < r["grade"] <= 3)
    flag = " ⚠️저평점 포함" if low else ""
    return f"[무신사 후기] 새 후기 {total}건 ({detail}){flag}"


def build_html(groups, config) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    watch = config.get("watch_keywords", [])

    def esc(s) -> str:
        return html.escape(str(s if s is not None else ""))

    out = [
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        "'Malgun Gothic',sans-serif;max-width:760px;margin:0 auto;color:#1a1a1a;line-height:1.6\">",
        f'<h2 style="margin:0 0 4px">새 후기 '
        f'{sum(len(g["items"]) for g in groups.values())}건</h2>',
        f'<p style="margin:0 0 20px;color:#666;font-size:13px">확인 시각 {now} (KST)</p>',
    ]

    # 요약 — 새 후기가 있는 상품만, 많은 순으로
    ranked = sorted((g for g in groups.values() if g["items"]),
                    key=lambda g: -len(g["items"]))
    out.append('<table style="border-collapse:collapse;width:100%;font-size:14px;margin-bottom:24px">')
    out.append('<tr style="background:#1f4e79;color:#fff">'
               '<th style="padding:8px;text-align:left">상품</th>'
               '<th style="padding:8px">새 후기</th>'
               '<th style="padding:8px">평균 평점</th>'
               '<th style="padding:8px">누적 후기</th></tr>')
    for g in ranked:
        graded = [r["grade"] for r in g["items"] if r["grade"] > 0]
        avg = f"{sum(graded)/len(graded):.2f}" if graded else "-"
        out.append(
            '<tr style="border-bottom:1px solid #e5e5e5">'
            f'<td style="padding:8px"><b>{esc(g["label"])}</b></td>'
            f'<td style="padding:8px;text-align:center">{len(g["items"])}</td>'
            f'<td style="padding:8px;text-align:center">{avg}</td>'
            f'<td style="padding:8px;text-align:center">{g["total"]}</td></tr>'
        )
    quiet = [g["label"] for g in groups.values() if not g["items"]]
    if quiet:
        out.append(f'<tr><td colspan="4" style="padding:8px;color:#999;font-size:12px">'
                   f'새 후기 없음: {esc(", ".join(quiet))}</td></tr>')
    out.append("</table>")

    # 개별 후기
    for g in ranked:
        rows = g["items"]
        out.append('<h3 style="margin:24px 0 8px;padding-bottom:6px;'
                   f'border-bottom:2px solid #1f4e79">{esc(g["label"])}'
                   f'<span style="font-weight:normal;font-size:12px;color:#888"> · '
                   f'<a href="{esc(g.get("url") or product_url(g))}" '
                   f'style="color:#888">후기 페이지</a></span></h3>')
        for r in rows:
            low = 0 < r["grade"] <= 3
            hits = [k for k in watch if k in r["content"]]
            border = "#d32f2f" if (low or hits) else "#e5e5e5"
            bg = "#fff5f5" if (low or hits) else "#fafafa"

            body = [
                f'<div style="border-left:4px solid {border};background:{bg};'
                'padding:12px 14px;margin-bottom:12px;border-radius:0 4px 4px 0">',
                '<div style="font-size:12px;color:#666;margin-bottom:6px">',
                f'<b style="color:#f5a623">{esc(stars(r["grade"]))}</b>',
                f' · {esc(r["created_at"])}',
            ]
            if r["size"]:
                body.append(f' · 사이즈 {esc(r["size"])}')
            if r["height"] and r["weight"]:
                body.append(f' · {esc(r["height"])}cm/{esc(r["weight"])}kg')
            if r["kind"] and r["kind"] != "일반":
                body.append(f' · <span style="color:#1f4e79">{esc(r["kind"])}</span>')
            if r["photos"]:
                body.append(f' · 사진 {r["photos"]}장')
            if r["likes"]:
                body.append(f' · 도움돼요 {r["likes"]}')
            body.append("</div>")

            if hits:
                body.append('<div style="font-size:12px;color:#d32f2f;margin-bottom:6px">'
                            f'⚠️ 주의 키워드: {esc(", ".join(hits))}</div>')
            body.append(f'<div style="font-size:14px;white-space:pre-wrap">{esc(r["content"])}</div>')
            body.append("</div>")
            out.append("".join(body))

    out.append(f'<p style="font-size:11px;color:#999;margin-top:24px">'
               f'musinsa-review-watcher · 감시 중인 상품 {len(config["products"])}개 · '
               '후기 본문은 원문 그대로이며 편집하지 않았습니다.</p>')
    out.append("</div>")
    return "".join(out)


def build_text(groups) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    ranked = sorted((g for g in groups.values() if g["items"]),
                    key=lambda g: -len(g["items"]))
    lines = [f"새 후기 {sum(len(g['items']) for g in groups.values())}건 · 확인 시각 {now} (KST)", ""]
    for g in ranked:
        graded = [r["grade"] for r in g["items"] if r["grade"] > 0]
        avg = f"{sum(graded)/len(graded):.2f}" if graded else "-"
        lines.append(f"- {g['label']}: {len(g['items'])}건, 평균 {avg} (누적 {g['total']}건)")
    lines.append("")
    for g in ranked:
        for r in g["items"]:
            head = f"[{g['label']}] {stars(r['grade'])} · {r['created_at']}"
            if r["size"]:
                head += f" · 사이즈 {r['size']}"
            lines += [head, r["content"], ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 메일 발송
# --------------------------------------------------------------------------- #

def send_mail(subject: str, text: str, html_body: str) -> None:
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    to = os.environ.get("MAIL_TO", "").strip() or user

    missing = [k for k, v in (("GMAIL_USER", user), ("GMAIL_APP_PASSWORD", password)) if not v]
    if missing:
        raise SystemExit(f"[error] 환경 변수 {', '.join(missing)} 가 없습니다. README 의 설정 절차를 확인하세요.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("무신사 후기 알림", user))
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, password)
        s.send_message(msg)
    print(f"[mail] 발송 완료 → {to}")


# --------------------------------------------------------------------------- #
# 메인
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="무신사 상품 후기 감시기")
    ap.add_argument("--init", action="store_true", help="알림 없이 현재 시점을 기준선으로 저장")
    ap.add_argument("--dry-run", action="store_true", help="메일을 보내지 않고 결과만 출력")
    ap.add_argument("--test-email", action="store_true", help="설정 확인용 테스트 메일 발송")
    ap.add_argument("--since-days", type=float, default=None, help="최근 N일치를 강제로 신규 취급")
    args = ap.parse_args()

    if args.test_email:
        send_mail(
            "[무신사 후기] 테스트 메일",
            "설정이 정상입니다. 새 후기가 올라오면 이 주소로 알림이 옵니다.",
            '<div style="font-family:sans-serif"><h3>설정이 정상입니다 ✅</h3>'
            "<p>새 후기가 올라오면 이 주소로 알림이 옵니다.</p></div>",
        )
        return 0

    config = load_config()
    state = load_state()
    max_pages = int(config.get("max_pages_per_run", 10))
    since = None
    if args.since_days is not None:
        since = datetime.now(KST) - timedelta(days=args.since_days)

    # 상품번호를 키로 쓴다. label 이 겹쳐도 서로 섞이지 않는다.
    groups = {}
    changed = False
    failed = []

    with requests.Session() as session:
        for i, product in enumerate(config["products"]):
            key = product_key(product)
            label = product["label"]
            prev = state.get(key, {})
            last_no = int(prev.get("last_no", 0))

            baseline = (last_no == 0 or args.init) and since is None
            pages = 1 if baseline else max_pages

            try:
                items, total = collect_new(product, last_no, pages, since, session)
            except RuntimeError as e:
                # 한 상품이 실패해도 나머지는 계속 확인한다.
                print(f"[{label}] 조회 실패: {e}", file=sys.stderr)
                failed.append(label)
                continue

            groups[key] = {
                "label": label,
                "goods_no": product["goods_no"],
                "url": product_url(product),
                "total": total,
                "items": [] if baseline else items,
            }

            newest = max([x["no"] for x in items], default=last_no)
            if newest != last_no or prev.get("total") != total:
                state[key] = {
                    "label": label,
                    "product": product.get("name", label),
                    "goods_no": product["goods_no"],
                    "last_no": max(newest, last_no),
                    "total": total,
                    "checked_at": datetime.now(KST).isoformat(timespec="seconds"),
                }
                changed = True

            note = " (기준선 설정)" if baseline else ""
            print(f"[{label}] 누적 {total}건 · 신규 {len(groups[key]['items'])}건{note}")

            if i < len(config["products"]) - 1:
                time.sleep(0.4)               # 상품 사이 간격

    if failed:
        print(f"[warn] {len(failed)}개 상품 조회 실패: {', '.join(failed)}", file=sys.stderr)

    new_total = sum(len(g["items"]) for g in groups.values())

    if new_total == 0:
        if changed and not args.dry_run:
            save_state(state)
        print("새 후기 없음")
        return 1 if failed else 0

    subject = build_subject(groups)
    text = build_text(groups)
    html_body = build_html(groups, config)

    if args.dry_run:
        print("\n" + "=" * 60)
        print("SUBJECT:", subject)
        print("=" * 60)
        print(text)
        return 0

    # 메일이 나간 뒤에 상태를 저장한다. 순서가 반대면 발송이 실패했을 때
    # 이미 읽음 처리된 후기를 다시는 알림받지 못한다.
    send_mail(subject, text, html_body)
    if changed:
        save_state(state)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
