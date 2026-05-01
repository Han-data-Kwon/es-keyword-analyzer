import os
import json
import requests
import anthropic
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── 클라이언트 초기화 ─────────────────────────────────────
anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SUPABASE_URL = os.environ.get("SUPABASE_URL")   # https://xxxx.supabase.co
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")   # anon key
TABLE        = "kw_ai_usage"
AI_LIMIT     = 5

KST = timezone(timedelta(hours=9))

def kst_today() -> str:
    """KST 기준 오늘 날짜 YYYY-MM-DD"""
    return datetime.now(KST).strftime("%Y-%m-%d")

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

# ── Supabase 헬퍼 ────────────────────────────────────────
def get_usage(date: str) -> dict:
    """해당 날짜 사용량 row 조회. 없으면 None."""
    res = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}",
        headers=sb_headers(),
        params={"date": f"eq.{date}", "select": "*"}
    )
    rows = res.json()
    return rows[0] if rows else None

def upsert_usage(date: str, count: int):
    """사용량 upsert (insert or update)."""
    requests.post(
        f"{SUPABASE_URL}/rest/v1/{TABLE}",
        headers={**sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={"date": date, "count": count}
    )

# ── 헬스체크 ─────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "keyword-insight-api"})

# ── 사용량 조회 ───────────────────────────────────────────
@app.route("/usage", methods=["GET"])
def check_usage():
    """현재 KST 기준 오늘 사용량 반환."""
    try:
        today = kst_today()
        row   = get_usage(today)
        used  = row["count"] if row else 0
        return jsonify({
            "ok":        True,
            "date":      today,
            "used":      used,
            "remaining": max(0, AI_LIMIT - used),
            "limit":     AI_LIMIT
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── AI 인사이트 생성 (사용량 차감 포함) ──────────────────
@app.route("/keyword-insight", methods=["POST"])
def keyword_insight():
    today = kst_today()

    # 1) 사용량 확인
    try:
        row  = get_usage(today)
        used = row["count"] if row else 0
    except Exception as e:
        return jsonify({"ok": False, "error": f"사용량 조회 실패: {str(e)}"}), 500

    if used >= AI_LIMIT:
        return jsonify({
            "ok":        False,
            "exhausted": True,
            "message":   f"오늘({today}) 팀 공유 일일 {AI_LIMIT}회 한도를 초과했습니다. KST 자정에 초기화됩니다."
        }), 429

    # 2) 사용량 선차감
    try:
        upsert_usage(today, used + 1)
    except Exception as e:
        return jsonify({"ok": False, "error": f"사용량 차감 실패: {str(e)}"}), 500

    # 3) 요청 파싱
    body = request.get_json()
    if not body:
        upsert_usage(today, used)
        return jsonify({"ok": False, "error": "요청 본문이 없습니다"}), 400

    sheet_name   = body.get("sheet_name", "")
    total_count  = body.get("total_count", 0)
    freq_table   = body.get("freq_table", "")
    sample_texts = body.get("sample_texts", "")

    if not freq_table:
        upsert_usage(today, used)
        return jsonify({"ok": False, "error": "freq_table이 비어 있습니다"}), 400

    # 4) Claude Haiku 호출
    prompt = f"""당신은 B2B 기업 출장/복지 데이터를 분석하는 전문 데이터 애널리스트입니다.
아래는 여기어때 기업 고객이 숙박 예약 시 입력한 키워드 데이터의 빈도 분석 결과입니다.

[시트명] {sheet_name}
[전체 데이터 건수] {total_count}건

[상위 키워드 빈도 TOP 20]
{freq_table}

[원문 샘플 (최대 10건 — 맥락 파악용)]
{sample_texts}

위 데이터를 분석하여 아래 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트는 절대 포함하지 마세요.

{{
  "sentiment": {{
    "positive": 숫자(0-100 정수, 긍정 비율%),
    "negative": 숫자(0-100 정수, 부정 비율%),
    "neutral": 숫자(0-100 정수, 중립 비율%),
    "reasoning": "판단 근거 1~2문장"
  }},
  "core_keywords": ["핵심키워드1", "핵심키워드2", "핵심키워드3", "핵심키워드4", "핵심키워드5"],
  "improvement_points": [
    {{"icon": "🔴", "text": "개선 포인트 1 (구체적 수치 포함)"}},
    {{"icon": "🟡", "text": "개선 포인트 2"}},
    {{"icon": "🟢", "text": "긍정 포인트 또는 유지 권장 사항"}}
  ],
  "executive_summary": "임원 보고용 2~3문장 요약. 수치 기반으로 핵심 패턴과 액션 포인트를 포함하세요."
}}"""

    try:
        message = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        parsed = json.loads(raw)
        return jsonify({
            "ok":        True,
            "result":    parsed,
            "used":      used + 1,
            "remaining": max(0, AI_LIMIT - used - 1),
            "limit":     AI_LIMIT,
            "date":      today
        })

    except json.JSONDecodeError as e:
        upsert_usage(today, used)
        return jsonify({"ok": False, "error": f"Claude 응답 파싱 실패: {str(e)}"}), 500
    except anthropic.APIError as e:
        upsert_usage(today, used)
        return jsonify({"ok": False, "error": f"Anthropic API 오류: {str(e)}"}), 500
    except Exception as e:
        upsert_usage(today, used)
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
