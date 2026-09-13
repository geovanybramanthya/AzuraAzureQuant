from pathlib import Path

p = Path('user_data/dashboard/index.html')
p.parent.mkdir(parents=True, exist_ok=True)
f = open(p, 'w', encoding='utf-8')
