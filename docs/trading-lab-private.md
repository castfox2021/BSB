> Historical read-only integration record. Current user-confirmed orders: [live-order-design.md](live-order-design.md).

# 실시간 수집 · Strategy Lab · Private 조회

2026-11-11. 현재 실행 경로는 README 기준. 이전 Task 01–03 및 manual-live-transition 기록은 당시 상태를 보존한 문서다.

## 데이터와 신호

공개 WS `wss://ws-api.bithumb.com/websocket/v1`의 실시간 Trade/Orderbook/Ticker를 기본으로 사용한다. 모든 KRW 마켓을 감시하고 분석 캔들은 거래대금 상위 20종목과 보유분을 우선 준비한다. REST는 마켓 목록과 초기 캔들·누락 복구·사용자가 요청한 백테스트 데이터에 사용한다. 매초 가격 REST 폴링은 하지 않는다.

UTC 1분봉을 실제 체결로 만들고 완료된 1분봉으로 3/5분봉을 합성한다. 거래 ID 중복 제거, 뒤늦은 체결·연결 공백의 봉 무효화, REST 절대값 대체로 중복 거래량을 방지한다. 미체결 분을 가짜 거래로 채우지 않는다. REST minute 캔들의 `to`는 공식 문서의 KST 형식을 따른다. 무효/불완전/누락 봉은 전략 입력에 사용하지 않는다.

수신 지연·처리 시간·최근 수신 경과·신호 계산 시간을 기록한다. 연결/REALTIME 여부, 15초 최신성, 시계 편차, 거래 정지·경고, 스프레드·호가 깊이·교차 호가, 캔들 복구 대기 조건을 점검한다. 데이터 이상 시 신규 매수를 중지하며 오래된 데이터로 보유분 매도 참고 가격을 만들지 않는다. 신호에는 근거·전략 버전·생성/만료 시각을 저장한다.

브라우저 스트림 `/ws/stream`은 1초 스냅샷이며 공개 WS를 대신하는 데이터 소스가 아니다. Web UI 소리/팝업은 권한과 열린 브라우저가 필요하다. 노트북 절전·프로그램 종료 중 거래소의 서버 손절이 제공되는 것은 아니다.

## Strategy Lab

전략별 독립 150,000원 자본으로 돌파/모멘텀/눌림 전략을 실험한다. 이전 완료 봉으로 판단하고 다음 봉 시가에 불리한 슬리피지와 양방향 수수료를 적용한다. 같은 봉에서 손절·익절 양쪽에 닿으면 손절 우선이며 갭은 불리한 가격을 사용한다. 결과는 승률·평균 손익·손익비·기대손익·MDD·수수료·슬리피지·순손익을 포함한다.

고정 데이터셋을 시간순 학습 70%/검증 30%로 나눈다. 각 구간 최소 40봉, 최대 20조합. 파라미터 선택은 학습 데이터만 이용하고 선택 후 검증 및 2배 비용 스트레스 결과를 기록한다. 데이터셋과 전략 버전은 해시로 고정한다. 검증 거래 5건 이상·양수 순손익·MDD 10% 이하·양수 비용 가정·72시간 유효 보고서와 정확한 승인 문구가 있어야 실전 **신호** 버전을 승격한다. 이전 버전 복원도 같은 검증을 거친다. 모의 자본·실전 수동 자본은 독립이다. 이전 버전의 보유 모의 포지션은 계속 관리하되 신규 실험 진입은 최근/활성 버전으로 한정한다.

수동/실시간 모의 비교의 MDD는 1분 표본(30일 보존)이며 틱 전체 기준이 아니다. 이력 조회는 제한된 최근 표본을 사용하므로 완전한 계좌 성과나 전략 인과 효과로 해석하지 않는다. 수동 등록 누락·외부 보유분은 자동으로 원장에 포함하지 않는다. 일일 위험 기준의 평가손익은 보유분 전체의 현재 평가를 포함하며 금일 시가 기준 변화만을 뜻하지 않는다.

## Private API

허용 GET: `/v1/accounts`, `/v1/orders/chance`, `/v1/order`, `/v1/orders`. 모든 요청/재시도에 새로운 HS256 JWT, UUID v4 nonce, 밀리초 timestamp. 실제 httpx 전송 query bytes와 동일한 SHA512 해시. 순서 보존·배열 `key[]=value`·Decimal 문자열·인코딩 단위 테스트를 둔다. 빈 파라미터는 해시 필드를 제외한다. 본문 서명용 직렬화도 순수 함수로 지원하지만 POST/DELETE 요청 기능은 없다.

키는 권한 600의 루트 `.env`에서만 읽는다. 미설정은 공개 데이터/수동 기능을 유지한다. 연동을 켰으나 권한·키 교체 확인·인증·통신·계좌 대조가 잘못되면 신규 매수 신호는 차단한다. API 원문 오류, JWT, Authorization, 자격정보 repr은 로그/응답에 내보내지 않는다. httpx/httpcore/WS 상세 로그는 억제하며 브라우저는 화이트리스트의 정규화한 필드만 받는다. 환경변수를 웹 실행 자식에 전달할 때 관련 비밀 이름을 제거한다. DB에도 키를 저장하지 않는다.

인증 오류 invalid_query_payload / jwt_verification / expired_jwt / NotAllowIP / out_of_scope는 안전한 고정 메시지로 표시한다. 일반 네트워크·5xx는 GET만 제한 재시도, 429/418은 대기한다. REST는 최대 초당 1회. Private WS v2 `wss://ws-api.bithumb.com/websocket/v2/private`의 `myOrder` 전체 종목 구독, PING/PONG20초·백오프·새 JWT 재인증을 사용한다. 매분 및 재연결/이벤트 후 REST를 통해 잔고·주문·체결을 복구한다. 재시작은 이전 확인 상태만으로 연결을 최신으로 간주하지 않는다.

`trade` 이벤트만 개별 체결로 기록하고 `done` 누적 수량을 체결로 중복 생성하지 않는다. (로컬 계정 영역, trade_id)로 REST/WS를 통합하고 동일 ID의 가격·수량 충돌은 오류 처리한다. WS 개별 수수료가 주문 전체 배분값보다 우선한다. 주문 전체 수수료는 모든 체결량이 확인된 경우에만 거래대금 비례 배분하고 끝 체결에 잔여분을 할당한다. 미확인 비용을 0으로 추정하지 않는다.

계좌 자산·수집 매매노트·전략 수동 원장은 별도다. 원장 대조는 전략 수량이 계좌 총 수량 이내인지, 전략 현금이 사용 가능 KRW 이내인지 확인한다. 거래/수수료/잔고 변화 시 대조 해제, 연결 실패·오래된 조회·복구 대기·미완료 이력 시 매수 차단. 자동 매매노트는 이전 보유 원가를 모르므로 전체 계좌 손익을 확정 계산하지 않는다. 전략 손익은 확인 등록한 전략 체결 원장을 기준으로 한다.

## 로컬 API 추가 경로

- GET `/api/private/status`, `/accounts`, `/orders`, `/journal?day=YYYY-MM-DD`, `/chance?market=KRW-BTC`
- POST `/api/private/sync` (조회 예약), `/reconcile` (문구 승인), `/notes` (체결 복기)
- GET/POST `/api/trading/control` (설계 모드/정지 상태, start 항상409)
- Lab `/api/lab/overview`, `/versions`, `/datasets`, `/runs`, `/jobs/{id}`, `/promote`, `/pause`

변경 요청은 로컬 Origin, JSON, `X-BSB-Local: 1` 필요. 브라우저는 `/api/manual/*` 서버 프록시로 호출한다. 키 입력·주문 생성/취소·출금 경로는 없다.

## 검증 범위

자동 테스트는 가짜 키/MockTransport와 로컬 WS로 JWT·인코딩·읽기 전용 경계·오류 비노출·중복 체결·재연결·분봉·백테스트 선행 정보 차단·최적화 학습 분리·버전 승격·수동 원장을 확인한다. 검증 결과: Python 49개, Jest 8개 통과. Ruff·mypy·ESLint·TypeScript·웹 프로덕션 빌드 통과. 공개 시세 실제 수신 490/490 KRW 종목, Private 미설정 및 주문 미지원 상태를 확인했다(`task-02-lab-local-verification.json`). 실제 Private 연결은 새 키가 로컬에 설정되지 않아 미검증이다. 소스 스캔은 실제 `.env` 내용을 읽지 않으며 현재 Git 저장소가 없어 Git 이력 검사 결과는 없다. 외부 라이브러리 TestClient deprecation 경고 1건이 남는다.

공식 참고: [인증](https://apidocs.bithumb.com/docs/인증-토큰-생성하기), [계좌](https://apidocs.bithumb.com/reference/전체-자산-조회), [주문 조회](https://apidocs.bithumb.com/reference/주문-리스트-조회), [개별 주문](https://apidocs.bithumb.com/reference/개별-주문-조회), [Private v2 체결](https://apidocs.bithumb.com/reference/내-주문-및-체결-myorder).
