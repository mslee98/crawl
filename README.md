# 당근마켓 검색 크롤러

당근마켓 검색 결과를 수집해 **리스트 + 상세 페이지**까지 크롤링한 뒤 CSV로 저장하는 크롤러입니다.

- **메인 크롤러**: `carrot-rough-crawl.py` — 검색어/키워드, 더보기 반복 → 상세 페이지 수집 → `results/` 에 타임스탬프 CSV 저장

---

## carrot-rough-crawl.py

검색어(또는 키워드 생략 시 전체 리스트)에 대해 **더보기** 버튼으로 카드를 모은 뒤, **각 글 상세 페이지**에 들어가 제목·본문·카테고리·판매자·채팅/관심/조회/매너온도 등을 추가 수집합니다. 카테고리 필터로 수집 대상을 줄여 크롤링 시간을 단축할 수 있습니다.

### 요구 사항

- Python 3.7+
- Playwright (Chromium)

### 설치

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

### 실행

```bash
source .venv/bin/activate
python carrot-rough-crawl.py --keyword 아이폰
```

| 옵션 | 설명 |
|------|------|
| `--keyword`, `-k` | 검색 키워드 (생략 시 전체 리스트) |
| `--categories`, `-c` | 수집할 카테고리 (쉼표 구분). 예: `디지털기기,티켓/교환권` |
| `--no-filter` | 카테고리 필터 없이 전체 수집 |

**실행 예시**

```bash
# 키워드만 (기본 카테고리 필터: 디지털기기, 남성패션/잡화, 티켓/교환권, e쿠폰)
python carrot-rough-crawl.py -k 노트북

# 수집할 카테고리 지정
python carrot-rough-crawl.py -k 아이폰 --categories 디지털기기,티켓/교환권

# 카테고리 필터 없이 전체 수집
python carrot-rough-crawl.py -k 아이폰 --no-filter

# 키워드 없이 전체 리스트 수집
python carrot-rough-crawl.py
```

- 실행 시 **브라우저 창이 뜹니다** (`HEADLESS=False`). 동작을 눈으로 확인할 수 있습니다.
- 콘솔에 리스트 수집 개수, 상세 수집 진행, 카테고리 필터 결과, **총 크롤링 시간**이 출력됩니다.

### 설정 (스크립트 상단)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `TARGET_COUNT` | `1000` | 리스트에서 수집 목표 카드 개수 |
| `HEADLESS` | `False` | `True` 면 브라우저 비표시 |
| `DETAIL_PAGE_DELAY_MS` | `800` | 상세 페이지 간 대기(ms) |
| `DETAIL_PAGE_TIMEOUT_MS` | `15000` | 상세 페이지 로딩 타임아웃(ms) |
| `ALLOWED_CATEGORIES` | `["디지털기기", "남성패션/잡화", "티켓/교환권", "e쿠폰"]` | 수집할 카테고리 (필터 사용 시) |

`ALL_CATEGORIES` 에 당근 전체 카테고리 목록이 정의되어 있으며, `--categories` 로 필요한 것만 선택할 수 있습니다.

### 출력 파일

- **경로**: `results/YYYY-MM-DD-HHMMSS.csv` (실행 시점 기준 타임스탬프)
- **인코딩**: UTF-8 BOM (엑셀에서 바로 열기 좋음)
- **컬럼**:

| 컬럼 | 설명 |
|------|------|
| `title` | 글 제목 (상세 페이지 기준) |
| `price` | 가격 |
| `location` | 지역/동네 (상세 페이지 기준) |
| `time` | 등록/갱신 시간 |
| `status` | 판매상태 (판매중 / 예약중 / 거래완료) |
| `category` | 카테고리 (예: 디지털기기, 여성잡화) |
| `seller_nickname` | 판매자 닉네임 |
| `description` | 글 본문 (상세 설명) |
| `image_count` | 이미지 개수 |
| `chat_count` | 채팅 수 |
| `interest_count` | 관심 수 |
| `view_count` | 조회 수 |
| `manner_temperature` | 매너 온도 (예: 39.7°C) |
| `url` | 글 상세 URL |

### 카테고리 필터

- **기본**: `ALLOWED_CATEGORIES` 에 있는 카테고리만 수집·저장 (디지털기기, 남성패션/잡화, 티켓/교환권, e쿠폰).
- 리스트 카드에 카테고리가 있으면 **상세 페이지 방문 전**에 필터해, 해당하지 않는 글은 상세 수집을 생략해 시간을 줄입니다.
- 카테고리를 알 수 없는 글은 상세에서 확인한 뒤, 허용된 카테고리만 최종 CSV에 포함됩니다.
- `--no-filter` 를 주면 카테고리 필터 없이 전체 수집합니다.

### 동작 흐름

1. 검색 URL(`/kr/buy-sell/?search=키워드` 또는 키워드 없이 리스트) 로 이동 후 `networkidle` 대기.
2. **더보기** 버튼 반복 클릭 → 목표 개수 도달 또는 더보기 없음/비활성화 시 종료.
3. 리스트 카드에서 `title`, `price`, `location`, `time`, `status`, `url`, `category`(있으면) 추출.
4. 카테고리 필터 사용 시: 허용 카테고리 또는 미확인만 상세 수집 대상으로 선택.
5. **상세 페이지** 방문: 제목·본문·카테고리·판매자·동네·채팅/관심/조회·매너온도·이미지 수 추출 후 항목에 병합.
6. 필터 사용 시 상세에서 확인한 카테고리로 한 번 더 걸러서, 허용된 것만 `results/YYYY-MM-DD-HHMMSS.csv` 로 저장.
7. **총 크롤링 시간** 출력 후 브라우저 종료.

---

## 기타 스크립트

- **deprecate/**: `daangn_scrape.py` 등 예전 URL 목록 기반 크롤러 (참고용).
- **title-classify.py**, **title-trans.py**: 제목 분류·번역 등 후처리용.

---

## (참고) pip SSL 인증서 에러

macOS에서 `SSLCertVerificationError`가 나면:

- 우회(비권장):  
  `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt`
- 권장: 시스템/파이썬 SSL 인증서 설정을 점검해 정상 동작하도록 맞추기.

---

## sentence-transformers 문장 변환기

### 한국어 특화 버전: snunlp/KR-SBERT-V40K-klueNLI-augSTS

- [snunlp/KR-SBERT-V40K-klueNLI-augSTS](https://huggingface.co/snunlp/KR-SBERT-V40K-klueNLI-augSTS)
