import re
import pandas as pd
import torch
from sentence_transformers import SentenceTransformer, util

# ==============================
# 1. 모델 로드
# ==============================
model = SentenceTransformer("snunlp/KR-SBERT-V40K-klueNLI-augSTS")

# ==============================
# 2. 전처리 함수
# ==============================
def preprocess(text):
    text = str(text).lower()
    text = text.replace("기가", "gb")
    text = text.replace("만원", "0000")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ==============================
# 3. 1차 룰 기반 분류
# ==============================
BRAND_KEYWORDS = [
    "구찌", "샤넬", "루이비통", "나이키", "리바이스",
    "프라다", "발렌시아가"
]

SKU_KEYWORDS = [
    "아이폰", "갤럭시", "맥북", "아이패드",
    "rtx", "그래픽카드", "플레이스테이션", "ps5"
]

def rule_based_classify(title):
    # 금액형
    if re.search(r"(상품권|기프티콘|문화상품권|백화점)", title):
        return "금액형", 1.0

    # SKU형
    for keyword in SKU_KEYWORDS:
        if keyword in title:
            return "SKU형", 1.0

    # 브랜드형
    for keyword in BRAND_KEYWORDS:
        if keyword in title:
            return "브랜드형", 1.0

    return None, None


# ==============================
# 4. 임베딩 기반 카테고리 샘플
# ==============================
CATEGORY_SAMPLES = {
    "SKU형": [
        "아이폰14 프로 256gb",
        "갤럭시 s23 울트라",
        "맥북 에어 m1",
        "rtx 3060 그래픽카드"
    ],
    "브랜드형": [
        "구찌 마몬트 숄더백",
        "샤넬 클래식 플랩백",
        "나이키 덩크 로우",
        "리바이스 514 청바지"
    ],
    "금액형": [
        "신세계 상품권 10만원권",
        "스타벅스 기프티콘",
        "문화상품권 5만원권"
    ],
    "기타": [
        "중고 의자 판매",
        "책상 팝니다",
        "유모차 판매"
    ]
}

# 카테고리 평균 벡터 생성
category_embeddings = {}

for category, samples in CATEGORY_SAMPLES.items():
    embeddings = model.encode(samples, convert_to_tensor=True)
    category_embeddings[category] = embeddings.mean(dim=0)


# ==============================
# 5. 임베딩 기반 분류
# ==============================
def embedding_classify(title, threshold=0.4):
    title_embedding = model.encode(title, convert_to_tensor=True)

    scores = {}
    for category, cat_embedding in category_embeddings.items():
        similarity = util.cos_sim(title_embedding, cat_embedding)
        scores[category] = float(similarity)

    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]

    if best_score < threshold:
        return "불확실", best_score

    return best_category, best_score


# ==============================
# 6. 통합 분류기
# ==============================
def classify_product(title):
    title = preprocess(title)

    # 1차 룰 기반
    rule_category, rule_score = rule_based_classify(title)
    if rule_category:
        return rule_category, rule_score

    # 2차 임베딩 기반
    return embedding_classify(title)


# ==============================
# 7. CSV 읽기
# ==============================
df = pd.read_csv("./result.csv")

if "title" not in df.columns:
    raise ValueError("CSV 파일에 'title' 컬럼이 필요합니다.")

# ==============================
# 8. 분류 실행
# ==============================
results = df["title"].apply(classify_product)

df["predicted_type"] = results.apply(lambda x: x[0])
df["similarity_score"] = results.apply(lambda x: x[1])

# ==============================
# 9. 결과 저장
# ==============================
df.to_csv("./classified_result2.csv", index=False)

print("✅ 분류 완료")
print(df.head())
