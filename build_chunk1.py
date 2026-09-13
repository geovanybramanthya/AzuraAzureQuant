from pathlib import Path
p = Path('user_data/dashboard/index.html')
p.parent.mkdir(parents=True, exist_ok=True)
with open(p, 'w', encoding='utf-8') as f:
    f.write(r'''<!DOCTYPE html>
<html lang= en class=dark>
<head>
  <meta charset=UTF-8 />
  <meta name=viewport content=width=device-width initial-scale=1.0 />
  <title>Apex Quantum | Autonomous Futures Command Center</title>
  <script src=https://cdn.tailwindcss.com></script>
  <script src=https://unpkg.com/lucide@latest></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap');
    body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #07090e; color: #e2e8f0; }
    .mono { font-family: 'JetBrains Mono', monospace; }
    .glass-panel { background: rgba(13, 17, 26, 0.85); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.07); }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0d111a; }
    ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 3px; }
  </style>
</head>
<body class=min-h-screen flex flex-col antialiased selection:bg-blue-600 selection:text-white>
''')
print('Chunk 1 OK')
