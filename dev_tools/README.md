# Dev Tools

## Setup
```bash
pip install -r requirements.txt
```

## bot_tester.py
Simulates bot users joining and playing a live multiplayer session.

```bash
python bot_tester.py ABC123
python bot_tester.py ABC123 --bots 10
python bot_tester.py ABC123 --url ws://localhost:8000/api/ws
```

Options:
- `session_code` (required): Session code from live multiplayer
- `--bots`: Number of bots (default: 5)
- `--url`: WebSocket URL (default: production)

Bots automatically answer questions with randomized accuracy (60-90%) and response times (1-8s).
