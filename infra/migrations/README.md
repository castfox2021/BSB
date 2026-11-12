# Market data migrations

소유 서비스는 Python 파이프라인이며 SQLAlchemy/Alembic을 사용합니다. NestJS/Prisma가 같은 테이블의 migration을 중복 소유하지 않습니다. 런타임 트랜잭션은 psycopg로 실행합니다.

Task 03: `0001_market_pipeline`은 원본 이벤트, markets, trades, orderbook snapshots, tickers, candles, cursor, 격리 데이터, 수집 coverage, 품질 이벤트를 만듭니다. 금액·수량은 NUMERIC, 시간은 TIMESTAMPTZ입니다. event_id와 source의 복합 고유 키를 사용합니다.

candle-builder 시작 시 migration을 실행하며, DB advisory lock으로 동시 upgrade를 직렬화합니다. 수동 적용:

```sh
docker compose exec candle-builder python -m bsb_service.pipeline.migrate
```

로컬 패키지 실행 시 DATABASE_URL이 필요하며, 설치된 패키지로 실행하면 BSB_PROJECT_ROOT가 contracts와 infra를 포함하는 저장소 루트를 가리켜야 합니다. Docker는 `/app`으로 설정합니다.

기존 버전을 덮어쓰지 않고 다음 revision을 추가합니다. downgrade는 테이블 데이터를 삭제하므로 자동 실행하지 않습니다. 실계좌·주문 테이블은 아직 만들지 않았습니다.
