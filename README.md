# A Grade Mind — updated uploaded application

This package updates agrademind(1).zip. It is the lightweight HTML/JavaScript + FastAPI application, not the separate Next.js/Supabase starter. Do not mix their databases or authentication implementations.

## Changes completed
- Teacher resets expire after 24 hours and force a new personal password before students can access tests.
- Reset and password-change operations invalidate previous tokens. Reset/change audit records contain actor, student, action and time, never passwords.
- New passwords must contain 10–128 characters. Existing passwords still work until changed.
- All PDF uploads become drafts. Flagged questions must be confirmed before publication. Existing flagged tests are hidden on upgrade.
- ALOC uses /api/v1/questions, X-API-Key, examType=jamb and uppercase/lowercase option normalization. Full CBT sessions require 60 English + 40 per other subject; incomplete banks fail explicitly.
- Objective marking remains deterministic against the saved key, out of 100. CBT results total four subject percentages out of 400; these are practice scores, not official JAMB results.

## Deployment
1. Back up any existing database. Use a dedicated PostgreSQL database (for example a Supabase server connection with SSL). Do not put connection credentials in browser code.
2. Put this folder in your repository. Configure a Python web service using render.yaml or equivalent.
4. Install with `pip install -r requirements.txt`.
5. Start with `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1` behind HTTPS. The single worker keeps the in-memory ALOC cache and login throttling consistent. PostgreSQL connection pool: 10 connections, 10 overflow.
6. Tables are created on startup, including additive auth_state and auth_audit tables. Existing user tables are preserved. Test upgrades against a database copy first.
7. Register Mr Alewi as a teacher with the private teacher code, then remove TEACHER_CODE from the environment to disable further teacher registration. Existing teacher logins continue working.
8. Confirm a student identity in person before using the reset screen. Give the temporary password privately. Never publish it in a class group.

## Checks before opening to students
- Test signup, reset, expiry, login and a real PDF on the deployed host.
- Confirm ALOC subject coverage and quota. IRS, History and Yoruba were not available under the previously tested mappings. Other subjects may also be unavailable depending on the account. No replacement questions are fabricated.
- ALOC pools are process-local and lost on restart. Prewarm and test the chosen subject combinations; check provider storage/use terms. Do not assume the free quota can serve an exam session.
- Conduct an 80-student rehearsal against the real PostgreSQL/hosting setup. This package has not been load-tested for 80 simultaneous users.
- PDF processing currently runs within the upload request. Large/slow uploads may time out and need retrying; a durable background queue is a future improvement.
- Phone ownership is not verified; no SMS is sent. Teacher-assisted recovery is intentional. Tokens remain in browser local storage; use trusted shared-device logout practices.

## Automated verification
`pip install pytest` then `python -m pytest test_app.py -q`.
Tests cover the complete reset/token/expiry flow, authorization, draft publishing gate, hidden answer keys, repeat submission stability and the ALOC request/response contract with mocked provider data. They do not call paid AI or prove live provider availability. JavaScript syntax was also checked with Node.

## Local development
Use Python 3.12, a virtual environment, and the values described in .env.example. Export environment variables before starting (the app does not automatically load .env). SQLite fallback is for local development only; use PostgreSQL for deployment. Never commit .env, database files or API keys.

## No-AI PDF tests
No AI provider or AI key is required. Provide 1–100 numbered questions with A–D or A–E options, followed by an Answer key section with entries such as 1. B. Every question needs an answer. Upload a text PDF (maximum 10 MB, 25 pages). Scans and image-bearing PDFs are rejected. Review the draft before publishing. Tests use the teacher key for deterministic marking.

Required server settings: DATABASE_URL, JWT_SECRET, ALOC_TOKEN; TEACHER_CODE for initial teacher registration. The existing Supabase application tables have been provisioned. Set the database connection privately in Render. Free hosting may still require provider account verification.
