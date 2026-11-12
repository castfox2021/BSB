import json
import os
from urllib.request import urlopen

for name, port, path in [('web', os.getenv('WEB_PORT', '3000'), '/api/health'),
                         ('api', os.getenv('API_PORT', '3001'), '/api/v1/system/health'),
                         ('python', os.getenv('PYTHON_PORT', '8000'), '/health'),
                         ('pipeline', os.getenv('PIPELINE_PORT', '8001'), '/health')]:
    with urlopen(f'http://127.0.0.1:{port}{path}', timeout=10) as response:
        data = json.load(response)
        assert data['status'] == 'ok', data
        assert data['dataSource'] == 'mock', data
        assert data['tradingEnabled'] is False, data
        print(f'{name}: healthy')
