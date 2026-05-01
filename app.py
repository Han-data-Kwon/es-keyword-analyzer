import os
import anthropic
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # GitHub Pages → Render CORS 허용

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "keyword-insight-api"})

@app.route("/keyword-insight", methods=["POST"])
def keyword_insight():
    try:
        body = request.get_json()
        if not body:
            return jsonify({"error": "요청 본문이 없습니다"}), 400

        sheet_name   = body.get("sheet_name", "")
        total_count  = body.get("total_count", 0)
        freq_table   = body.get("freq_table", "")   # 문자열로 받음
        sample_texts = body.get("sample_texts", "")  # 문자열로 받음 (최대 10건)

        if not freq_table:
            return jsonify({"error": "freq_table이 비어 있습니다"}), 400

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

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",  # 비용 효율 — 인사이트 요약엔 Haiku로 충분
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        # JSON 펜스 제거
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        import json
        parsed = json.loads(raw)
        return jsonify({"ok": True, "result": parsed})

    except json.JSONDecodeError as e:
        return jsonify({"error": f"Claude 응답 파싱 실패: {str(e)}", "raw": raw}), 500
    except anthropic.APIError as e:
        return jsonify({"error": f"Anthropic API 오류: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
