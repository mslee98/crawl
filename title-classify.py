# #!/usr/bin/env python3
# """
# 당근 result.csv 상품 제목 → 브랜드 + 계층 분류 (한국어 LLM)

# 사용 예:
#   # 로컬 Ollama (한국어 모델 추천: eeve-korean, kullm 등)
#   pip install openai
#   ollama run eeve-korean-10.8b
#   OPENAI_API_BASE=http://localhost:11434/v1 OPENAI_MODEL=eeve-korean-10.8b python title-classify.py

#   # OpenAI 또는 Upstage 등 (API 키 필요)
#   OPENAI_API_KEY=sk-... python title-classify.py
#   OPENAI_API_KEY=... OPENAI_API_BASE=https://api.upstage.ai/v1 OPENAI_MODEL=solar-1-mini-chat python title-classify.py
# """
# import csv
# import json
# import os
# import re
# import sys
# from pathlib import Path

# try:
#     from openai import OpenAI
# except ImportError:
#     print("pip install openai 필요")
#     sys.exit(1)

# # -----------------------------
# # 설정 (환경변수 우선)
# # -----------------------------
# INPUT_CSV = os.environ.get("INPUT_CSV", "result.csv")
# OUTPUT_CSV = os.environ.get("OUTPUT_CSV", "result_classified.csv")
# OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")  # Ollama: http://localhost:11434/v1
# OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "ollama")  # Ollama는 키 없어도 동작하도록
# OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")  # Ollama: eeve-korean-10.8b 등
# BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5"))  # 한 번에 몇 개 제목 처리할지
# MAX_RETRIES = 2

# # -----------------------------
# # 프롬프트 (한국어로 지시)
# # -----------------------------
# SYSTEM_PROMPT = """당신은 중고 거래 상품 제목을 보고 브랜드와 카테고리를 추출하는 전문가입니다.
# 규칙:
# - 브랜드: 상품에 명시된 브랜드명만 추출. 없으면 빈 문자열 "".
# - 대분류: 가전/전자, 의류/패션, 가방/잡화, 화장품, 식품, 스포츠, 도서/문구, 완구/피규어, 기타 등 넓은 범주.
# - 중분류: 노트북, 가방, 운동화, 맨투맨, 스킨케어 등 세부 유형.
# - 소분류: 15인치, 미니, 블랙, 32인치 등 스펙/세부 속성. 없으면 "".

# 예시:
# - "샤넬 자개 로고 펄핑크 동그리 백" → brand: "샤넬", 대: "가방/잡화", 중: "가방", 소: "자개 로고"
# - "맥북에어15" → brand: "애플", 대: "가전/전자", 중: "노트북", 소: "맥북에어 15인치"
# - "블랙 조거 팬츠" → brand: "", 대: "의류/패션", 중: "바지", 소: "조거"

# 반드시 각 상품마다 한 줄씩, 아래 JSON 형식만 출력하세요. 설명 없이 JSON만.
# {"brand":"...", "category_large":"...", "category_mid":"...", "category_small":"..."}
# """

# USER_PROMPT_TEMPLATE = """다음 중고 상품 제목들에서 브랜드와 카테고리(대/중/소)를 추출해주세요.
# 각 제목마다 한 줄의 JSON으로 출력해주세요. 총 {n}개입니다.

# 제목 목록:
# {titles}
# """


# def parse_price(price_str: str) -> str:
#     """가격 문자열 그대로 반환 (CSV용)."""
#     return (price_str or "").strip()


# def extract_json_lines(text: str) -> list[dict]:
#     """응답 텍스트에서 한 줄씩 JSON 객체 파싱."""
#     results = []
#     for line in text.strip().split("\n"):
#         line = line.strip()
#         if not line or line.startswith("#"):
#             continue
#         # JSON 블록만 추출 (```json ... ``` 제거)
#         if "```" in line:
#             continue
#         # {...} 패턴 찾기
#         match = re.search(r"\{[^{}]*\}", line)
#         if match:
#             try:
#                 results.append(json.loads(match.group()))
#             except json.JSONDecodeError:
#                 pass
#     return results


# def classify_batch(client: OpenAI, titles: list[str], model: str) -> list[dict]:
#     """여러 제목을 한 번에 분류."""
#     numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
#     user = USER_PROMPT_TEMPLATE.format(n=len(titles), titles=numbered)

#     for attempt in range(MAX_RETRIES):
#         try:
#             resp = client.chat.completions.create(
#                 model=model,
#                 messages=[
#                     {"role": "system", "content": SYSTEM_PROMPT},
#                     {"role": "user", "content": user},
#                 ],
#                 temperature=0.1,
#             )
#             text = (resp.choices[0].message.content or "").strip()
#             rows = extract_json_lines(text)
#             # 개수 맞추기: 부족하면 빈 항목 채우기
#             while len(rows) < len(titles):
#                 rows.append({
#                     "brand": "",
#                     "category_large": "",
#                     "category_mid": "",
#                     "category_small": "",
#                 })
#             return rows[: len(titles)]
#         except Exception as e:
#             if attempt + 1 == MAX_RETRIES:
#                 # 실패 시 빈 분류로 채움
#                 return [
#                     {"brand": "", "category_large": "", "category_mid": "", "category_small": ""}
#                     for _ in titles
#                 ]
#             continue
#     return []


# def build_path(row: dict) -> str:
#     """category_path 문자열 생성 (예: 애플 > 맥북 에어 > 15인치)."""
#     parts = []
#     if row.get("brand"):
#         parts.append(row["brand"])
#     for key in ("category_large", "category_mid", "category_small"):
#         v = (row.get(key) or "").strip()
#         if v and v not in parts:
#             parts.append(v)
#     return " > ".join(parts)


# def main():
#     base = Path(__file__).resolve().parent
#     input_path = base / INPUT_CSV
#     output_path = base / OUTPUT_CSV

#     if not input_path.exists():
#         print(f"입력 파일 없음: {input_path}")
#         sys.exit(1)

#     client = OpenAI(
#         base_url=OPENAI_API_BASE,
#         api_key=OPENAI_API_KEY,
#     )

#     rows = []
#     with open(input_path, "r", encoding="utf-8-sig") as f:
#         reader = csv.DictReader(f)
#         fieldnames = list(reader.fieldnames or [])
#         rows = list(reader)

#     # 기존 필드 + 분류 필드
#     extra = ["brand", "category_large", "category_mid", "category_small", "category_path"]
#     out_fieldnames = fieldnames + extra

#     total = len(rows)
#     print(f"총 {total}건 분류 시작 (배치 크기: {BATCH_SIZE}, 모델: {OPENAI_MODEL})")

#     for i in range(0, total, BATCH_SIZE):
#         batch = rows[i : i + BATCH_SIZE]
#         titles = [b.get("title", "").strip() for b in batch]
#         if not any(titles):
#             for b in batch:
#                 b["brand"] = b["category_large"] = b["category_mid"] = b["category_small"] = ""
#                 b["category_path"] = ""
#             continue

#         classified = classify_batch(client, titles, OPENAI_MODEL)
#         for b, c in zip(batch, classified):
#             b["brand"] = c.get("brand", "") or ""
#             b["category_large"] = c.get("category_large", "") or ""
#             b["category_mid"] = c.get("category_mid", "") or ""
#             b["category_small"] = c.get("category_small", "") or ""
#             b["category_path"] = build_path(c)
#         print(f"  진행: {min(i + BATCH_SIZE, total)}/{total}")

#     with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
#         w = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
#         w.writeheader()
#         w.writerows(rows)

#     print(f"저장 완료: {output_path}")


# if __name__ == "__main__":
#     main()
