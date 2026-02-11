# 당근마켓 검색 크롤러

당근마켓 검색 결과를 수집해 CSV로 저장하는 크롤러입니다.

- **테스트용 간단 크롤러**: `carrot-rough-crawl.py` (단일 검색어, 더보기 반복 → CSV)
- **URL 목록 기반 크롤러**: `daangn_scrape.py` (여러 URL, `daangn_urls.txt` 기반)

---

## carrot-rough-crawl.py (테스트용 크롤러)

검색어 하나에 대해 **더보기** 버튼을 반복 클릭해 목표 개수만큼 카드를 수집한 뒤, 한 번에 추출해 `result.csv`로 저장합니다.

### 요구 사항

- Python 3.7+
- Playwright (Chromium)

### 설치

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install playwright
python -m playwright install chromium
```

### 실행

```bash
source .venv/bin/activate
python carrot-rough-crawl.py
```

- 실행 시 **브라우저 창이 뜹니다** (`headless=False`). 동작을 눈으로 확인할 수 있습니다.
- 콘솔에 현재 수집 개수와 종료 사유(목표 도달 / 더보기 없음 등)가 출력됩니다.

### 설정 (스크립트 상단)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `TARGET_COUNT` | `1000` | 수집 목표 카드 개수 |
| `SEARCH_URL` | `https://www.daangn.com/search/아이폰` | 당근마켓 검색 URL (검색어 변경 시 여기 수정) |

원하면 `ITEM_SELECTOR`, `MORE_BUTTON_SELECTOR`도 수정 가능합니다.

### 출력 파일: `result.csv`

- **경로**: 프로젝트 루트의 `result.csv`
- **인코딩**: UTF-8 BOM (엑셀에서 바로 열기 좋음)
- **컬럼**:
  - `title` – 글 제목
  - `price` – 가격
  - `location` – 지역(동네)
  - `time` – 등록/갱신 시간
  - `status` – 판매상태 (`판매중` / `예약중` / `거래완료`)
  - `url` – 글 상세 URL

### 동작 흐름

1. `SEARCH_URL` 로 이동 후 `networkidle` 대기
2. **더보기** 버튼 반복 클릭  
   - 현재 카드 수가 `TARGET_COUNT` 이상이거나  
   - 카드 수가 더 이상 늘지 않거나  
   - 더보기 버튼이 없/비활성화면 종료
3. 페이지 내 모든 카드에 대해 JS로 `title`, `price`, `location`, `time`, `status`, `url` 추출
4. `result.csv` 저장 후 브라우저 종료

---

## daangn_scrape.py (URL 목록 기반)

여러 리스트/검색 URL을 `daangn_urls.txt`에 넣고, 한 번에 크롤링해 CSV로 저장할 때 사용합니다.

### 한 번만 세팅

```bash
chmod +x setup.sh
./setup.sh
```

- 가상환경 `.venv`, 패키지 설치, Playwright Chromium, `out/daangn` 폴더 생성
- `daangn_urls.txt`가 없으면 `daangn_urls.example.txt`를 복사

### URL 넣기

- `daangn_urls.txt`에 당근 리스트/검색 URL을 **한 줄에 하나씩** 추가
- `#`으로 시작하는 줄은 주석(무시)

### 실행

```bash
source .venv/bin/activate
python daangn_scrape.py --urls daangn_urls.txt --out out/daangn/listings.csv --limit 100
```

- 브라우저를 보려면 끝에 `--headed` 추가
- 결과: `out/daangn/listings.csv`

---

## (참고) pip SSL 인증서 에러

macOS에서 `SSLCertVerificationError`가 나면:

- 우회(비권장):  
  `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt`
- 권장: 시스템/파이썬 SSL 인증서 설정을 점검해 정상 동작하도록 맞추기
