# Operations

모든 컨테이너 내부 시간은 UTC입니다. `.env`는 Git 제외 대상입니다. 기본 자격증명은 로컬 개발 전용이며 운영 배포 시 교체합니다.

| 변수 | Compose 기본값 | 설명 |
|---|---|---|
| POSTGRES_USER | bsb | DB 사용자 |
| POSTGRES_PASSWORD | bsb_local_only | 로컬 전용 비밀번호, URL 특수문자는 percent encoding 필요 |
| POSTGRES_DB | bsb | DB명 |
| DATA_SOURCE | mock | Task 01은 다른 값 거부 |
| WEB_PORT | 3000 | 호스트 웹 포트 |
| API_PORT | 3001 | 호스트 API 포트 |
| PYTHON_PORT | 8000 | 수집 템플릿 호스트 포트 |
| DATABASE_URL | Compose가 조립 | API/Python 필수, 로컬 직접 실행 시 별도 지정 |
| REDIS_URL | redis://redis:6379/0 | API/Python 필수, 로컬 직접 실행 시 별도 지정 |
| SERVICE_NAME | Compose가 지정 | Python 서비스 구분 |

```sh
docker compose logs -f --tail 100
docker compose restart scanner
docker compose down
```

`down`은 볼륨을 유지합니다. `down -v`는 데이터를 삭제하므로 복구 가능한 개발 데이터에만 사용합니다. Python 의존성 장애 로그는 traceback, NestJS는 기본 Logger와 표준 예외 응답을 사용합니다. Health는 재시도되며 컨테이너 프로세스 종료 시 restart 정책이 적용됩니다. unhealthy만으로 자동 재시작하지는 않습니다.

Task 02 전 공식 빗썸 문서의 Public REST/WS 형식·구독 수·호출 제한·재연결을 확인해야 합니다. 인증·TLS·모니터링을 포함한 운영 배포 강화는 Task 10 범위입니다.


## Task 02 수집기 설정

| 변수 | 기본값 | 허용 / 의미 |
|---|---|---|
| COLLECTOR_SOURCE | mock | mock(유휴), bithumb_public(공개 실시간 시세) |
| COLLECTOR_BATCH_SIZE | 100 | 1–100, 연결당 내부 분할 크기 |
| COLLECTOR_REFRESH_SECONDS | 300 | 30–86400, 종목 목록 갱신 간격 |
| COLLECTOR_STALE_SECONDS | 15 | 1–300, 이벤트 신선도·latest TTL |
| COLLECTOR_RECEIVE_TIMEOUT | 30 | 5–120, 무응답 재연결 시간 |
| COLLECTOR_STREAM_MAXLEN | 100000 | 100–1000000, 이벤트 Stream 최대 길이 |

live collector도 거래는 수행하지 않습니다. 외부 접속 장애 시 재시도하며 live Health는 전송 상태에 따라 503을 반환합니다. mock 모드는 외부에 연결하거나 가짜 이벤트를 생성하지 않습니다.

`clock_skew_detected`와 shard `last_lag_seconds`를 점검합니다. 교환 시각이 로컬 수신보다 1초 넘게 앞서면 해당 이벤트를 신선 데이터로 인정하지 않고 `clock_skew_events`에 집계합니다. 양쪽 시계 차이는 로컬 NTP/chrony 상태와 거래소 시각을 함께 확인해야 하며, 임의로 타임스탬프를 보정하지 않습니다. 캔들 복구 전 `history_complete` 및 `entries_allowed`는 항상 false입니다.

실시간 모드 검사: `python3 tests/integration/live_collector_smoke.py`. 기존 `smoke.py`는 기본 mock 구성 전용입니다. 외부 API 검증은 CI 기본 단위 테스트와 분리되어 있습니다.
