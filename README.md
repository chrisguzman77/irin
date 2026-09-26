# Irin

Irin is a Raspberry Pi bedside device for type 1 diabetics on injections: it shows live glucose, predicts lows 30 minutes ahead, wakes the sleeper with an LED frame and sound, and writes a plain-language morning report. Irin Rounds (the Impiricus challenge) turns what happens at home into short, encrypted Clinical Signal Cards for the patient's own doctor. Night Buddy and Family Story (the Meta challenge) connect the people around the patient: a human backstop on the alarm ladder, and a plain-language story of the night for family. One system, three audiences.

## Quickstart

```bash
git clone <this repo> irin && cd irin
cp .env.example .env                       # placeholders are fine for the mock backend
uv venv --python 3.14 .venv && source .venv/bin/activate
uv pip install -r backend/requirements.txt
cd backend && IRIN_HW=mock uvicorn app.main:app --reload
# open http://localhost:8000/  (the kiosk display page, replay data, DEMO badge)
```

The hosted app (Node 22):

```bash
cd frontend/app && npm install && npm run dev   # http://localhost:5173
```

Optional: the relay (`uvicorn main:app --port 8100 --reload` from `relay/`), Irin Cloud (`--port 8200` from `cloud/`), or both plus Caddy and local databases with `docker compose -f deploy/docker-compose.yml up`; the static role pages under `frontend/clinician/`, `frontend/watch/`, `frontend/family/` need only a static server.

## Where to read next

- `CLAUDE.md` — the rules every session obeys (lanes, invariants, git tiers, journal).
- `docs/plans/` — one build plan per lane (chris, justin, george, slavik).
- `docs/Irin_Spec.pdf`, `docs/Irin_Rounds.pdf`, `docs/Irin_Night_Buddy.pdf`, `docs/FAMILY_STORY.md` — the specs.
