# 무신사 후기 감시기

무신사 상품에 새 후기가 올라오면 이메일로 알려줍니다. GitHub Actions에서 2시간마다 자동 실행되므로 PC를 켜둘 필요가 없습니다.

현재 감시 대상은 **무신사 스탠다드 린넨 라이크 와이드 데님 팬츠**의 두 색상입니다.

| 색상 | 상품번호 | 상품 페이지 |
|---|---|---|
| 블랙 | `4780658` | https://www.musinsa.com/products/4780658 |
| 그레이 | `3845650` | https://www.musinsa.com/products/3845650 |

---

## 알림 예시

제목: `[무신사 후기] 새 후기 3건 (블랙 1건, 그레이 2건) ⚠️저평점 포함`

본문에는 색상별 건수·평균 평점·누적 후기 수 요약 표가 먼저 나오고, 그 아래 후기 **전문**이 평점·작성일시·사이즈·키/몸무게·체험단 여부와 함께 붙습니다. 3점 이하이거나 주의 키워드(냄새, 물빠짐, 이염, 불량, 반품 등)가 포함된 후기는 빨간 테두리로 강조됩니다.

---

## 설정 (처음 한 번만)

### 1. 저장소 만들고 코드 올리기

이 폴더 전체를 새 GitHub 저장소에 올립니다. 공개(public) 저장소면 Actions 사용량이 무제한 무료입니다.

```bash
cd musinsa-review-watcher
git init
git add .
git commit -m "무신사 후기 감시기"
git branch -M main
git remote add origin https://github.com/<GitHub아이디>/musinsa-review-watcher.git
git push -u origin main
```

> 비공개(private) 저장소도 됩니다. 무료 계정 기준 월 2,000분이 제공되는데, 이 작업은 한 번에 1분 남짓이라 2시간 주기(월 약 360회)면 여유롭습니다.

### 2. Gmail 앱 비밀번호 발급

Gmail은 계정 비밀번호로 외부 프로그램 로그인을 허용하지 않습니다. 전용 비밀번호를 따로 받아야 합니다.

1. Google 계정에 **2단계 인증**이 켜져 있어야 합니다. (https://myaccount.google.com/security)
2. https://myaccount.google.com/apppasswords 접속
3. 앱 이름에 `musinsa-watcher` 입력 후 생성
4. 표시되는 **16자리 문자열**을 복사 (창을 닫으면 다시 볼 수 없습니다)

### 3. 저장소에 Secrets 등록

저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| 이름 | 값 |
|---|---|
| `GMAIL_USER` | `thisman01@gmail.com` (보내는 계정) |
| `GMAIL_APP_PASSWORD` | 2단계에서 받은 16자리 |
| `MAIL_TO` | 받을 주소. 생략하면 `GMAIL_USER`로 보냄. 쉼표로 여러 명 가능 |

### 4. 동작 확인 및 기준선 설정

저장소 → **Actions** 탭 → 왼쪽에서 **무신사 후기 감시** 선택 → **Run workflow**

순서대로 두 번 실행하세요.

1. 모드 **`test-email`** → 메일이 오는지 확인 (설정 점검)
2. 모드 **`init`** → 알림 없이 현재 시점을 기준선으로 저장

`init`을 건너뛰어도 첫 실행 때 최근 20건이 한꺼번에 오는 정도라 큰 문제는 없지만, 깔끔하게 시작하려면 실행해 두는 편이 좋습니다.

이후로는 2시간마다 알아서 돌고, 새 후기가 있을 때만 메일이 옵니다.

---

## 감시 대상 바꾸기

`config.json`만 고치면 됩니다. 다른 무신사 상품도 상품번호만 넣으면 그대로 동작합니다.

```json
{
  "products": [
    { "label": "블랙",   "name": "린넨 라이크 와이드 데님 팬츠 [블랙]",   "goods_no": 4780658 },
    { "label": "그레이", "name": "린넨 라이크 와이드 데님 팬츠 [그레이]", "goods_no": 3845650 }
  ],
  "max_pages_per_run": 10,
  "watch_keywords": ["냄새", "물빠짐", "이염", "불량", "반품", "교환", "실망", "후회"]
}
```

- `goods_no` — 상품 URL `musinsa.com/products/<번호>`의 번호
- `watch_keywords` — 후기 본문에 이 단어가 있으면 메일에서 빨간색으로 강조
- `max_pages_per_run` — 한 번에 최대 몇 페이지(1페이지 = 20건)까지 거슬러 올라갈지

상품을 추가하거나 바꿨다면 `state.json`에서 해당 항목을 지우거나, `init` 모드로 한 번 돌려 기준선을 다시 잡으세요.

주기를 바꾸려면 `.github/workflows/watch.yml`의 cron을 수정합니다. (`0 */2 * * *` = 2시간마다, `0 * * * *` = 1시간마다, `0 9 * * *` = 매일 오전 9시 — **UTC 기준**이라 한국 시간은 +9시간)

---

## 어떻게 동작하나

무신사 후기 목록 API를 그대로 씁니다. 로그인이나 브라우저 자동화가 필요 없습니다.

```
GET https://goods.musinsa.com/api2/review/v1/view/list
      ?goodsNo=4780658&selectedSimilarNo=4780658&sort=new&page=0&pageSize=20
```

두 가지가 중요합니다.

- **`selectedSimilarNo`를 `goodsNo`와 같게** 넣어야 해당 색상만 걸러집니다. 빼면 같은 시리즈의 다른 색상 후기가 섞여 나옵니다.
- **`pageSize`는 20이 상한**입니다. 21 이상은 API가 거부합니다.

신규 판별은 시간이 아니라 **후기 번호(`no`) 기준**입니다. 상품별로 마지막에 본 최대 번호를 `state.json`에 기록해 두고 그보다 큰 번호만 새 후기로 봅니다. 그래서

- GitHub Actions 실행이 몇십 분 늦어져도 누락되지 않고
- 같은 후기가 두 번 알림되지 않으며
- 며칠 쉬었다 돌려도 그동안 쌓인 후기를 전부 잡아냅니다

상태는 실행이 끝날 때마다 봇이 저장소에 커밋합니다. **메일 발송이 성공한 뒤에** 저장하므로, 발송이 실패하면 상태가 그대로 남아 다음 실행에서 다시 시도합니다.

---

## 로컬에서 실행하기

```bash
pip install -r requirements.txt

export GMAIL_USER=thisman01@gmail.com
export GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx     # Windows PowerShell: $env:GMAIL_USER="..."

python watcher.py --test-email      # 메일 설정 확인
python watcher.py --init            # 기준선 설정 (알림 없음)
python watcher.py --dry-run         # 메일 없이 결과만 출력
python watcher.py                   # 실제 실행
python watcher.py --since-days 3    # 최근 3일치를 강제로 다시 알림 (점검용)
```

로직 검증은 네트워크 없이 돌아갑니다.

```bash
python selftest.py
```

---

## 문제가 생기면

| 증상 | 확인할 것 |
|---|---|
| 메일이 안 옴 | Actions 탭에서 실행 로그 확인. `test-email` 모드로 설정부터 점검 |
| `535 Authentication failed` | 계정 비밀번호를 넣은 경우입니다. 앱 비밀번호 16자리를 다시 발급받으세요 |
| `state.json` 커밋 실패 | Settings → Actions → General → Workflow permissions를 **Read and write**로 변경 |
| 다른 색상 후기가 섞임 | `selectedSimilarNo` 파라미터가 빠진 경우입니다 |
| 스케줄이 안 돎 | 공개 저장소는 60일간 커밋이 없으면 스케줄이 자동 중지됩니다. 이 작업은 상태 파일을 계속 커밋하므로 해당 없음 |

GitHub Actions의 cron은 정시 보장이 아닙니다. 러너가 붐비면 수 분에서 수십 분 늦게 돌 수 있는데, 번호 기준 판별이라 결과에는 영향이 없습니다.

---

## 파일 구성

```
├── watcher.py                    감시 본체
├── config.json                   감시 대상 상품 · 주의 키워드
├── state.json                    마지막으로 본 후기 번호 (자동 갱신)
├── selftest.py                   네트워크 없이 도는 로직 검증
├── requirements.txt
└── .github/workflows/watch.yml   2시간 주기 스케줄
```
