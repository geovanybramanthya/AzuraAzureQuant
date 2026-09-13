import urllib.request

try:
    with urllib.request.urlopen('http://127.0.0.1:5050/') as response:
        content = response.read()
        print('HTTP Status:', response.status)
        print('HTML Size:', len(content), 'bytes')
        print('First 100 bytes:', content[:100].decode('utf-8'))
except Exception as e:
    print('Verification Error:', e)
