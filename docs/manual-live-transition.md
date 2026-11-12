# 수동 매매 도우미로 방향 전환

> 이 문서는 초기 로컬 전환 당시의 기록입니다. 현재 구현과 실행 방법은 [README](../README.md) 및 [최신 구현 기록](trading-lab-private.md)을 따릅니다.

기준일: 2026-11-10 (KST).

사용자 요청: 노트북에서 신호·알림을 받고, 실제 매수·매도·손절은 빗썸에서 직접 실행하며 결과를 보고하는 방식으로 전환합니다. 첨부 v2 문서의 시작 명령을 자동 실행 권한으로 취급하지 않고, 현재 요청에 맞춰 로컬 실행 기반을 우선 추가했습니다. 자동 주문 금지는 유지합니다.

## 구현

- 독립 FastAPI + SQLite 서비스: `services/manual-assistant`. 기존 PostgreSQL/Redis 패키지를 설치할 필요가 없습니다.
- Next.js 수동 매매 화면, 고정 수동 주문 문구, 실제 잔고와 설정 자본 구분, OFFLINE/신호 비활성 표시
- SQLite schema version 1, 설정/감사 로그와 후속 신호·체결·포지션 테이블 준비. 금액은 canonical Decimal 문자열
- 설정 변경 검증과 감사 로그의 단일 트랜잭션. 잘못된 정책·unknown key·부동소수점 금액 거부
- 로컬 Host/CORS 제한, same-origin JSON + 사용자 지정 헤더 요구, 웹 프록시의 외부 업스트림 차단
- 수동 DEMO 브라우저 알림·소리 테스트 및 화면 대체 안내 (자동 알림 아님)
- 프로젝트 내부 Python 3.12/uv 환경, lockfile, 웹/API 함께 시작·종료하는 실행기

기존 웹 테스트 도구 Jest를 재사용했습니다. Vitest로 별도 전환하지 않았습니다.

## API (127.0.0.1:18000)

- GET /api/health — MANUAL LIVE, OFFLINE, signals_enabled=false, orders_supported=false
- GET /api/settings, PATCH /api/settings — 정책 검증·원자적 저장·감사 로그
- GET /api/market/status, /api/market/candidates, /api/signals — 미연결/비활성 상태와 빈 목록
- GET /api/positions, /api/trades, /api/risk/status — 실제 잔고 미확인 및 감시 미작동

POST /api/manual-fills와 자동 주문 경로는 아직 제공하지 않습니다. 테이블이 존재한다고 체결 등록·포지션 계산 기능이 구현된 것은 아닙니다. `/api/health`는 로컬 프로그램과 DB 상태이며 거래소 시세 상태를 대신하지 않습니다. 기존 웹 `/api/health`는 이전 프로젝트 호환용이며 새 화면은 `/api/manual/health` 프록시를 사용합니다.

브라우저는 같은 출처의 /api/manual/*를 사용합니다. 직접 API의 PATCH를 호출하려면 허용 Origin, X-BSB-Local: 1, Content-Type: application/json이 필요합니다. 외부 공개용 인증 서비스가 아니므로 바인딩을 0.0.0.0으로 바꿔 공개하지 않습니다.

## 환경변수

| 변수 | 기본값 | 의미 |
|---|---|---|
| MANUAL_WEB_PORT | 13000 | 로컬 웹 |
| MANUAL_API_PORT | 18000 | 로컬 API |
| BSB_MANUAL_DB | 저장소/data/manual-assistant.sqlite3 | SQLite 경로 |
| BSB_MANUAL_ORIGINS | localhost/127.0.0.1 웹 포트 | 실행기가 설정하는 허용 HTTP Origin |
| MANUAL_API_URL | http://127.0.0.1:18000 | 서버 측 웹 프록시 대상, 로컬 주소만 허용 |

다른 포트 사용 예: `MANUAL_WEB_PORT=13001 MANUAL_API_PORT=18001 npm run manual:dev`.

## 검증 및 한계

Python 14개 테스트와 Jest 8개 테스트, lint·타입 검사·웹 빌드가 통과했습니다. 노트북의 관리형 Python 3.12.16에서 Docker 없이 실행했습니다. 직접 API와 웹 프록시 Health 200, 설정 조회, Ctrl+C 후 두 포트 종료 및 재시작을 확인했습니다. 실제 브라우저에서 설정 자본과 비활성 상태 표시를 확인했습니다.

현재 시세 연결·1/3/5분 캔들·스캐너·매수/매도 신호·수동 체결 UI·포지션/손익 계산·실제 손절 경보는 미구현입니다. 알림 테스트는 시세 또는 수익성 검증이 아닙니다. 기존 TestClient의 외부 라이브러리 deprecation 경고 1건이 남아 있습니다. 원격 CI 실행은 하지 않았으며 로컬 검증 결과입니다.

## 보존한 이전 작업

이전 Task 03 PostgreSQL 파이프라인 코드는 삭제하지 않고 보존했습니다. 방향 전환 전 Python 단위/실DB 통합 검사 72개가 통과했습니다. 보관 중인 실제 시세 표본 500건 재생은 **호가 500건, 캔들 0건**이었으므로 실제 체결 기반 캔들 생성 검증이라고 보고하지 않습니다(`task-03-public-replay.json`). SQLite로 자동 이관하지 않았습니다.

기존 Docker Compose는 별도 레거시 실행 경로이며 새 로컬 앱에 필요 없습니다. 노트북 자원 절약을 위해 BSB 컨테이너는 중지했고 코드·개발 DB·볼륨은 보존했습니다. 이전 단계의 전용 테스트 DB와 임시 컨테이너만 정리했습니다. v1 전체 완료나 v2 실전 운영 준비 완료를 의미하지 않습니다.

## 후속 범위

공식 API 재확인과 공개 시세 연결 → SQLite 1/3/5분 캔들 → 스캐너·신호와 지연 차단 → 사용자 확인 체결 등록·부분 매도·순손익 → 손절/익절/손실 경보 → 모의 신호 관찰 및 검증 순서입니다. 실전 신호를 활성화하기 전 시계 차이, 수수료·호가 정책 및 장애 시 동작을 검증해야 합니다.
