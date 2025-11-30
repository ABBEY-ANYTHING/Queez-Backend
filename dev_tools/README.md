# Dev Tools

## Setup
```bash
pip install -r requirements.txt
```

## bot_tester.py
Simulates bot users joining and playing a live multiplayer session.
All bots answer each question together (synchronized), with 2.5s delay between questions.

```bash
# Basic usage (5 bots)
python bot_tester.py ABC123

# More bots
python bot_tester.py ABC123 --bots 20

# Custom batching (for many bots)
python bot_tester.py ABC123 --bots 50 --batch 10 --delay 1.0

# Local testing
python bot_tester.py ABC123 --url ws://localhost:8000/api/ws
```

Options:
- `session_code` (required): Session code from live multiplayer
- `--bots`: Number of bots (default: 5)
- `--batch`: Bots per connection batch (default: 10)
- `--delay`: Delay between batches in seconds (default: 1.0)
- `--url`: WebSocket URL (default: production)

Configuration (edit in file):
- `BOT_ACCURACY_MIN/MAX`: Answer accuracy range (default: 60%-90%)
- `RESPONSE_TIME_MIN/MAX`: Think time range (default: 1-4 seconds)
- `QUESTION_DELAY`: Delay between questions (default: 2.5 seconds)

Features:
- All bots answer each question together (synchronized)
- Waits for all bots to complete before showing results
- Waits 15 seconds before exiting (so host can see final leaderboard)
- Handles disconnections gracefully
- Batched connections to avoid overwhelming server
