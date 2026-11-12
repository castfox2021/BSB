from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from bsb_service.app import app, settings


def test_reject_live(monkeypatch):
    monkeypatch.setenv('DATA_SOURCE', 'live')
    with pytest.raises(ValueError):
        settings()


@pytest.mark.parametrize('failed,status', [(False, 200), (True, 503)])
def test_health(failed, status):
    check = AsyncMock(side_effect=RuntimeError('offline') if failed else None)
    with patch('bsb_service.app.check_dependencies', check):
        response = TestClient(app).get('/health')
    assert response.status_code == status
    assert response.json()['tradingEnabled'] is False
