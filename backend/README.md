VANTAGE Exchange - Backend (FastAPI)

Requirements

- Python 3.9+

Install

```bash
python -m pip install -r requirements.txt
```

Run

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Run everything (backend + bot) for development

```bash
python run_all.py
```

Endpoints

- `GET /api/transactions` - list transactions
- `GET /api/transaction/{id}` - get transaction
- `POST /api/transaction/{id}/complete` - mark completed
- `POST /api/transaction/{id}/reject` - mark rejected
- `GET /proofs/{filename}` - serve proof files

Notes

The backend reads the same SQLite database used by the bot: `techbit_v2.db` and the proof files from `proof_files/`.
