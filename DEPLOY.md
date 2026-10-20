# Deployment handoff

Upload the CONTENTS of this folder to the repository root so render.yaml is at the root.
Connect that repository to a Render Blueprint. The Blueprint selects free hosting for initial testing. Capacity for 80 simultaneous students is not verified.

## Required private settings in Render
- DATABASE_URL: PostgreSQL connection string. For Supabase use Connect → Session pooler and SSL (`sslmode=require` or stricter verification). URL-encode special characters in the database password. Use a dedicated project and the table-owner connection, not the anon API key.
- JWT_SECRET: generated automatically by the Blueprint.
- TEACHER_CODE: a strong secret used only to create the first teacher account; unset after setup.
- ALOC_TOKEN: your ALOC API key.

APP_ENV=production and Python version are in render.yaml. Startup fails if the database, signing key or API configuration is missing. A missing database must never fall back to SQLite in production. The app enables row-level security on its tables; students use the FastAPI endpoints, not direct database access. The backend database role must own these tables.

## Launch acceptance
1. Back up any previous database; deploy to a separate staging database first.
2. Confirm /api/health returns 200 (it tests the database connection).
3. Create the owner teacher account, then remove TEACHER_CODE.
4. Create a test student and verify reset → forced password change → old token rejection.
5. Upload a short real PDF, review all extracted questions, publish, attempt it and confirm /100 score.
6. Run a full four-subject CBT and check 180 questions, hidden keys and /400 result.
7. Check every offered subject and provider quota; unsupported subjects cannot be promised.
8. Rehearse with 80 test accounts on the actual host; measure response time/errors while students save and submit. Do not claim capacity from the unit tests.
9. Set backup retention and test restoration in the database account before real student records accumulate.

Local automated checks: `pip install -r requirements.txt pytest` then `python -m pytest -q`.


Reference documentation:
https://render.com/docs/blueprint-spec
https://supabase.com/docs/guides/database/connecting-to-postgres

## No-AI PDF tests
No AI provider or AI key is required. Provide 1–100 numbered questions with A–D or A–E options, followed by an Answer key section with entries such as 1. B. Every question needs an answer. Upload a text PDF (maximum 10 MB, 25 pages). Scans and image-bearing PDFs are rejected. Review the draft before publishing. Tests use the teacher key for deterministic marking.

Required server settings: DATABASE_URL, JWT_SECRET, ALOC_TOKEN; TEACHER_CODE for initial teacher registration. The existing Supabase application tables have been provisioned. Set the database connection privately in Render. Free hosting may still require provider account verification.
