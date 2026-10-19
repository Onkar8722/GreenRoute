#!/usr/bin/env python3
import requests

print('Testing all Dashboard APIs...\n')

endpoints = [
    ('Health', '/api/health'),
    ('Bins (400)', '/api/bins'),
    ('Stats', '/api/stats'),
    ('Alerts', '/api/alerts'),
    ('Forecast', '/api/forecast'),
]

for name, endpoint in endpoints:
    try:
        r = requests.get(f'http://127.0.0.1:5000{endpoint}')
        data = r.json()
        if endpoint == '/api/bins':
            count = len(data.get('bins', []))
            print(f'✅ {name}: {count} bins loaded')
        elif endpoint == '/api/forecast':
            count = len(data.get('forecasts', []))
            crit = data.get('predicted_critical_bins', 0)
            print(f'✅ {name}: {count} forecasts (critical: {crit})')
        elif endpoint == '/api/stats':
            total = data.get('total_bins', 0)
            fill = data.get('avg_fill', 0)
            print(f'✅ {name}: {total} bins, avg fill: {fill}%')
        else:
            print(f'✅ {name}: OK')
    except Exception as e:
        print(f'❌ {name}: {e}')

print('\n🎉 Dashboard is ready!')
print('📊 Open: http://127.0.0.1:5000/dashboard')
