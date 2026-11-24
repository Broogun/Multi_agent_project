# 02. 스펙

인터페이스와 데이터 형식. 바꾸면 같은 PR에서 이 문서를 고치고, 관련 담당자를 리뷰어로 넣는다.

## 검색 창 이벤트 (search → app)

검색 모듈이 UI로 보내는 유일한 형식. 에이전트에게는 `panel_id`와 "표시함" 사실만 돌아간다.

```json
{
  "type": "search_panel",
  "panel_id": "8자리",
  "query": "상품명 + 용량 (개인정보 없음)",
  "text": "Gemini 그라운딩 답변 (수정 금지)",
  "suggestions_html": "Google 검색 제안 HTML (그대로 렌더링)",
  "sources": [{"title": "...", "uri": "..."}],
  "notice": "검색 결과이며, 실제 가격은 판매처에서 확인하세요."
}
```

## 사용자 확인 가격 (app → data)

| 필드 | 필수 | 설명 |
|---|---|---|
| user_id | ✅ | |
| product_name | ✅ | |
| size, unit_size, unit | | 용량 (단위가격 계산용) |
| price | ✅ | 원, 정수 |
| seller | | 판매처명 |
| checked_date | ✅ | 사용자가 확인한 날 |
| source_type | ✅ | `user_verified` / `receipt_ocr` / `capture_ocr` |

저장하지 않는 것: 상품 설명, 이미지, 페이지 텍스트, 검색 결과. 캡처 원본은 확인 후 삭제.

## 계산 결과 형식 (data → agent)

```json
{"status": "ok | needs_check | no_data | stale", "value": {}, "calc": {"항목": "계산식 문자열"},
 "sources": [{"id": "", "name": "", "as_of": ""}], "as_of": "", "caveats": []}
```
`status`가 `needs_check`/`no_data`면 에이전트는 숫자를 말하지 않는다.

## 미정
- 에이전트 그래프 노드 목록 (A)
- 사용자 데이터 저장 형식 (B)
