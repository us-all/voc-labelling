# Phase 2: VOC Intelligence Demo — 분류 데이터 활용 증명

날짜: 2026-03-17

## 배경

편지글/게시글/채널톡 3개 데이터 소스에 대해 분류 파이프라인을 구축했다.

- 편지글/게시글: 2-axis (topic 4분류 + sentiment 3분류) + detail_tags (category_tags, free_tags, summary)
- 채널톡: 2-depth (route 규칙 기반 + topics 5분류 multi-label)
- 비용 최적화: KcELECTRA FT + LLM fallback → 월 $1 수준

**이제 증명해야 할 것**: 이 분류 데이터로 실제 비즈니스 의사결정에 쓸 수 있는 분석이 가능하다.

## 목적

1. 분류 데이터를 활용한 **다양한 분석/활용 방식**이 가능함을 데모로 증명
2. 부서별(운영/콘텐츠/서비스) 각각 다른 뷰로 **액셔너블 인사이트**를 전달할 수 있음을 보여줌
3. 기존 LLM 전수 읽기 방식 대비 **속도/비용/일관성** 개선을 증명
4. 향후 확장 가능성(Slack, 자동 알림 등) 구조를 제시

## 왜 지금인가

- 3개 데이터 소스 분류 파이프라인 완성
- 비용 최적화 달성 (KcELECTRA + LLM fallback)
- 분류 체계 안정화 (2-axis + 2-depth)
- 다음 단계는 "이걸로 뭘 할 수 있는가" 증명
