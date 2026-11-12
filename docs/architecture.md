# Architecture

웹 → NestJS 조회/관리 API → PostgreSQL/Redis. Python은 수집 → 캔들 → 스캐너 → 신호 → 위험관리 → 모의체결, 백테스트를 책임집니다. 이 연결은 후속 구현 계획이며 Task 01은 Health와 실행 기반만 제공합니다.

7개 Python 컨테이너는 같은 설치 가능한 서비스 템플릿을 사용하며 SERVICE_NAME으로 구별합니다. 도메인 책임 구현 시 각각 별도 진입점을 추가합니다. NestJS가 거래소 수집·전략을 수행하지 않습니다.

이벤트 기준은 `packages/contracts/market-trade.schema.json`입니다. Decimal은 JSON 문자열, 시각은 UTC `Z` 형식이며 exchange_at과 received_at을 분리합니다. Python 검증기는 JSON Schema와 Decimal을 사용합니다. TypeScript 인터페이스는 컴파일 시 계약이며 런타임 소비자는 향후 동일 Schema 검증을 적용해야 합니다. UI 거래 시각 표시는 후속 Task에서 Asia/Seoul을 사용합니다.

업무 테이블과 계좌 상태는 아직 없습니다. PostgreSQL·Redis만 named volume으로 보존됩니다.


## Task 02 구현 상태

공개 REST 종목 조회 → 100종목 단위 WebSocket 분할 → Decimal/UTC 정규화 → bounded Redis Stream 및 TTL latest 캐시가 구현되었습니다. 수집기만 별도 Python 진입점(`bsb_service.collector.app`)을 사용합니다. 종목 목록 변경 시 재구독하고, 재연결 시 연결 세대와 신선도 추적을 초기화합니다.

수집 전송 상태와 데이터 신뢰도는 구분합니다. 현재 모든 출력은 history_complete=false 및 entries_allowed=false입니다. PostgreSQL 시세 저장·캔들·영구 중복 방지·누락 보정은 다음 단계입니다. 거래소 체결 ID는 순서나 연속성을 보장하지 않아 ID 숫자 간격을 누락으로 판단하지 않습니다.
