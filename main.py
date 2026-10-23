"""A Grade Mind backend: auth, ALOC questions, server-timed CBT, teacher PDF -> test."""
import os, re, json, time, random, base64, hashlib, hmac, threading
from collections import defaultdict
import httpx, jwt
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, JSON, update, text
from sqlalchemy.orm import sessionmaker, declarative_base

# ---------- config ----------
DB = os.getenv("DATABASE_URL", "sqlite:///./agrademind.db")
for old in ("postgres://", "postgresql://"):
    if DB.startswith(old):
        DB = DB.replace(old, "postgresql+psycopg2://", 1)
SECRET = os.environ["JWT_SECRET"]  # required
TEACHER_CODE = os.getenv("TEACHER_CODE", "")
ALOC = os.getenv("ALOC_BASE", "https://dev.aloc.com.ng/api/v1")
ALOC_KEY = os.getenv("ALOC_TOKEN", "")
PRODUCTION = os.getenv("APP_ENV") == "production"
if PRODUCTION:
    if not DB.startswith("postgresql+psycopg2://"):
        raise RuntimeError("Production requires DATABASE_URL pointing to PostgreSQL.")
    if len(SECRET) < 32 or SECRET.startswith("change-me"):
        raise RuntimeError("Production JWT_SECRET must be a random value of at least 32 characters.")
    if not ALOC_KEY:
        raise RuntimeError("Set ALOC_TOKEN before production startup.")
EXAM_MIN = int(os.getenv("EXAM_MINUTES", "120"))
GRACE = 30  # seconds allowed after the deadline for the final submit

kw = ({"connect_args": {"check_same_thread": False}} if DB.startswith("sqlite")
      else {"pool_size": 10, "max_overflow": 10, "pool_pre_ping": True})
engine = create_engine(DB, **kw)
Session = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    phone = Column(String(11), unique=True, index=True, nullable=False)
    username = Column(String(40), nullable=False)
    pw = Column(String(300), nullable=False)
    role = Column(String(10), default="student")
    created = Column(Float, default=time.time)


class Test(Base):
    __tablename__ = "tests"
    id = Column(Integer, primary_key=True)
    title = Column(String(200))
    subject = Column(String(80))
    minutes = Column(Integer, default=60)
    published = Column(Boolean, default=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True)
    created = Column(Float, default=time.time)


class TQ(Base):
    __tablename__ = "test_questions"
    id = Column(Integer, primary_key=True)
    test_id = Column(Integer, ForeignKey("tests.id"), index=True)
    num = Column(Integer)
    q = Column(String)
    o = Column(JSON)
    a = Column(Integer)
    review = Column(Boolean, default=False)


class Attempt(Base):
    __tablename__ = "attempts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    kind = Column(String(8))  # aloc | test
    test_id = Column(Integer, index=True, nullable=True)
    title = Column(String(200))
    deadline = Column(Float)
    submitted = Column(Boolean, default=False)
    late = Column(Boolean, default=False)
    qdata = Column(JSON)
    answers = Column(JSON, default=dict)
    result = Column(JSON, nullable=True)
    score = Column(Float, nullable=True)
    created = Column(Float, default=time.time)


class AuthState(Base):
    __tablename__ = "auth_state"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    version = Column(Integer, nullable=False, default=0)
    must_change = Column(Boolean, nullable=False, default=False)
    reset_expires = Column(Float, nullable=True)


class AuthAudit(Base):
    __tablename__ = "auth_audit"
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(40), nullable=False)
    created = Column(Float, default=time.time)


Base.metadata.create_all(engine)
# Existing flagged tests must also respect the publishing gate after upgrade.
with Session.begin() as session:
    if engine.dialect.name == "postgresql":
        # Backend connects as table owner; public Supabase Data API roles receive no policies.
        for table in Base.metadata.sorted_tables:
            session.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))
    session.execute(update(Test).where(Test.id.in_(
        session.query(TQ.test_id).filter(TQ.review == True)
    )).values(published=False))

app = FastAPI(title="A Grade Mind")


def db():
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ---------- auth ----------
def hp(pw):
    salt = os.urandom(16)
    return salt.hex() + ":" + hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def vp(pw, stored):
    salt, h = stored.split(":")
    t = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
    return hmac.compare_digest(t, h)


def auth_state(s, uid):
    state = s.get(AuthState, uid)
    if state is None:
        state = AuthState(user_id=uid, version=0, must_change=False)
        s.add(state)
        s.flush()
    return state


def valid_password(value):
    if not 10 <= len(value) <= 128:
        raise HTTPException(422, "Use a password between 10 and 128 characters.")


def token(u, state):
    return jwt.encode({"sub": str(u.id), "ver": state.version,
                       "exp": int(time.time()) + 12 * 3600}, SECRET, "HS256")


def me(request: Request, s=Depends(db)):
    h = request.headers.get("authorization", "")
    try:
        claims = jwt.decode(h[7:], SECRET, algorithms=["HS256"])
        uid = int(claims["sub"])
    except Exception:
        raise HTTPException(401, "Please log in again.")
    u = s.get(User, uid)
    if not u:
        raise HTTPException(401, "Please log in again.")
    state = s.get(AuthState, uid)
    if claims.get("ver", 0) != (state.version if state else 0):
        raise HTTPException(401, "Your password changed. Please log in again.")
    if state and state.must_change:
        if not state.reset_expires or time.time() > state.reset_expires:
            raise HTTPException(401, "Temporary password expired. Ask your teacher for another reset.")
        if request.url.path not in ("/api/auth/change-password", "/api/auth/me"):
            raise HTTPException(403, "Change your temporary password before continuing.")
    return u


def teacher(u=Depends(me)):
    if u.role != "teacher":
        raise HTTPException(403, "Teachers only.")
    return u


class Reg(BaseModel):
    phone: str
    username: str
    password: str
    role: str = "student"
    teacher_code: str = ""


class Login(BaseModel):
    phone: str
    password: str


FAILS = {}


def user_out(u, s):
    state = auth_state(s, u.id)
    s.commit()
    return {"token": token(u, state), "user": {"id": u.id, "username": u.username,
            "role": u.role, "must_change_password": state.must_change}}


@app.get("/api/auth/me")
def profile(u=Depends(me), s=Depends(db)):
    state = s.get(AuthState, u.id)
    return {"id": u.id, "username": u.username, "role": u.role,
            "must_change_password": bool(state and state.must_change)}


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/change-password")
def change_password(b: ChangePassword, u=Depends(me), s=Depends(db)):
    valid_password(b.new_password)
    user = s.query(User).filter_by(id=u.id).with_for_update().one()
    if len(b.current_password) > 128 or not vp(b.current_password, user.pw):
        raise HTTPException(400, "Current password is incorrect.")
    if b.current_password == b.new_password:
        raise HTTPException(422, "Choose a different password.")
    state = auth_state(s, user.id)
    user.pw = hp(b.new_password)
    state.version += 1
    state.must_change, state.reset_expires = False, None
    s.add(AuthAudit(actor_id=user.id, student_id=user.id, action="password_changed"))
    return user_out(user, s)


@app.post("/api/auth/register")
def register(b: Reg, s=Depends(db)):
    phone = re.sub(r"\s", "", b.phone)
    if not re.fullmatch(r"0\d{10}", phone):
        raise HTTPException(422, "Enter a valid 11-digit Nigerian phone number.")
    valid_password(b.password)
    if len(b.username.strip()) < 3:
        raise HTTPException(422, "Username needs at least 3 characters.")
    if b.role == "teacher" and (not TEACHER_CODE or not hmac.compare_digest(b.teacher_code, TEACHER_CODE)):
        raise HTTPException(403, "Wrong teacher code.")
    if s.query(User).filter_by(phone=phone).first():
        raise HTTPException(409, "This phone number is already registered. Log in instead.")
    u = User(phone=phone, username=b.username.strip()[:40], pw=hp(b.password),
             role="teacher" if b.role == "teacher" else "student")
    s.add(u); s.commit()
    return user_out(u, s)


@app.post("/api/auth/login")
def login(b: Login, s=Depends(db)):
    phone = re.sub(r"\s", "", b.phone)
    n, t0 = FAILS.get(phone, (0, time.time()))
    if time.time() - t0 > 600:
        n, t0 = 0, time.time()
    if n >= 8:
        raise HTTPException(429, "Too many attempts. Wait a few minutes.")
    u = s.query(User).filter_by(phone=phone).first()
    if not u or len(b.password) > 128 or not vp(b.password, u.pw):
        FAILS[phone] = (n + 1, t0)
        raise HTTPException(401, "Wrong phone number or password.")
    state = auth_state(s, u.id)
    if state.must_change and (not state.reset_expires or time.time() > state.reset_expires):
        raise HTTPException(401, "Temporary password expired. Ask your teacher for another reset.")
    FAILS.pop(phone, None)
    return user_out(u, s)


# ---------- text cleaning (questions may carry HTML) ----------
TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)[^>]*>")
OK = ("sub", "sup", "b", "i", "u", "br")


def clean(t):
    t = TAG.sub(lambda m: f"<{m.group(1)}{m.group(2).lower()}>" if m.group(2).lower() in OK else "", str(t or ""))
    return re.sub(r"<(?!/?(?:sub|sup|b|i|u|br)>)", "&lt;", t).strip()


# ---------- ALOC ----------
SLUG = {"Use of English": "english", "Mathematics": "mathematics", "Biology": "biology", "Chemistry": "chemistry",
        "Physics": "physics", "Economics": "economics", "Government": "government", "Commerce": "commerce",
        "Accounts": "accounting", "Literature in English": "literature", "Christian Religious Studies": "crs",
        "Islamic Religious Studies": "irs", "History": "history", "Geography": "geography",
        "Yoruba": "yoruba"}


def norm(x):
    try:
        options = x.get("options")
        if not isinstance(options, dict):
            return None
        o = {str(k).lower(): v for k, v in options.items()}
        keys = [k for k in "abcde" if str(o.get(k, "")).strip()]
        answer = str(x.get("correctAnswer", "")).strip().lower()
        if not x.get("text") or len(keys) < 2 or answer not in keys:
            return None
        img = str(x.get("imageUrl") or "")
        passage = x.get("section") or x.get("passage") or ""
        if x.get("hasPassage") and not isinstance(passage, str):
            return None
        if x.get("hasPassage") and not passage:
            return None
        return {"id": x.get("id"), "q": clean((passage + "\n\n" if passage else "") + x["text"]),
                "o": [clean(o[k]) for k in keys], "a": keys.index(answer),
                "sol": clean(x.get("explanation")), "img": img if img.startswith("https://") else ""}
    except (TypeError, ValueError):
        return None


def _aloc_page(slug, n, cursor=None):
    if not ALOC_KEY:
        raise HTTPException(503, "ALOC_TOKEN is not set on the server.")
    try:
        r = httpx.get(ALOC.rstrip("/") + "/questions",
                      params={"subject": slug, "examType": "jamb", "limit": min(n, 15), **({"cursor": cursor} if cursor else {})}, timeout=20,
                      headers={"X-API-Key": ALOC_KEY, "Accept": "application/json"})
        if r.status_code != 200:
            detail = ""
            try:
                payload = r.json()
                if isinstance(payload, dict):
                    detail = payload.get("message") or payload.get("error") or ""
                if not isinstance(detail, str):
                    detail = ""
                detail = detail.replace(ALOC_KEY, "[redacted]")
                detail = re.sub(r"(?i)(?:aloc_|alc_)[a-z0-9_-]+", "[redacted]", detail)
                detail = re.sub(r"https?://\S+", "[provider link]", detail)
                detail = re.sub(r"[\x00-\x1f\x7f]", " ", detail)[:300]
            except ValueError:
                pass
            reason = f" {detail}" if detail else " Please contact your teacher."
            raise HTTPException(502, f"Question provider returned HTTP {r.status_code} for {slug}.{reason}")
        items = r.json().get("data", [])
        if not isinstance(items, list):
            raise ValueError("Invalid provider response")
        out = [q for q in map(norm, items) if q]
        if not out:
            raise HTTPException(502, "No usable questions available for this subject.")
        return out, r.json().get("pagination", {})
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Question provider is unavailable. Please try later.")


def fetch_aloc(slug, n=40):
    questions, cursor, seen_cursors = {}, None, set()
    # Bounded pagination; never keep requesting the same page or inflate scores
    # by repeating questions. ALOC caps each page at 15.
    for _ in range(12):
        rows, pagination = _aloc_page(slug, min(15, n - len(questions)), cursor)
        for q in rows:
            questions[q["id"] if q["id"] is not None else q["q"]] = q
        if len(questions) >= n:
            break
        cursor = pagination.get("nextCursor")
        if not pagination.get("hasMore") or not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
    return list(questions.values())[:n], "/questions"


POOL, LOCKS = {}, defaultdict(threading.Lock)


def get_q(slug, n):
    # Reuse a subject bank across students instead of refilling in background
    # on every exam start. This avoids wasting the provider's free quota.
    with LOCKS[slug]:
        p = POOL.setdefault(slug, {})
        if len(p) < n:
            items, _ = fetch_aloc(slug, n)
            for q in items:
                p[q["id"] if q["id"] is not None else q["q"]] = q
        vals = list(p.values())
    if len(vals) < n:
        raise HTTPException(503, "Not enough distinct questions for a full exam. Please ask your teacher to check the question-bank plan.")
    return random.sample(vals, n)


@app.get("/api/health")
def health(s=Depends(db)):
    try:
        s.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "Database unavailable.")
    return {"ok": True}


@app.get("/api/aloc/test")
def aloc_test(u=Depends(teacher)):
    items, path = fetch_aloc("chemistry", 5)
    return {"working_endpoint": path, "got": len(items), "sample": items[0]}


# ---------- attempts ----------
def payload(a):
    return {"id": a.id, "kind": a.kind, "title": a.title, "deadline": a.deadline, "now": time.time(),
            "answers": a.answers or {},
            "questions": [{"s": q["s"], "q": q["q"], "o": q["o"], "img": q.get("img", "")} for q in a.qdata]}


def mark(a, ans):
    by = {}
    for i, q in enumerate(a.qdata):
        b = by.setdefault(q["s"], {"c": 0, "n": 0})
        b["n"] += 1
        b["c"] += ans.get(str(i)) == q["a"]
    subs = [{"s": k, "c": v["c"], "n": v["n"], "p": round(v["c"] / v["n"] * 100)} for k, v in by.items()]
    total = sum(x["p"] for x in subs) if a.kind == "aloc" else subs[0]["p"]
    review = [{"s": q["s"], "q": q["q"], "o": q["o"], "a": q["a"], "you": ans.get(str(i)), "sol": q.get("sol", "")}
              for i, q in enumerate(a.qdata)]
    return {"subjects": subs, "total": total, "max": 400 if a.kind == "aloc" else 100, "review": review}


def finalize(s, a, ans, late=False):
    a.result = mark(a, ans)
    a.score, a.answers, a.submitted, a.late = a.result["total"], ans, True, late
    s.commit()
    return a.result


def clean_answers(raw, n):
    out = {}
    for k, v in (raw or {}).items():
        if str(k).isdigit() and int(k) < n and type(v) is int and 0 <= v <= 4:
            out[str(k)] = v
    return out


def active(s, uid):
    for a in s.query(Attempt).filter_by(user_id=uid, submitted=False).order_by(Attempt.id.desc()).all():
        if time.time() > a.deadline + GRACE:
            finalize(s, a, a.answers or {}, late=True)
        else:
            return a
    return None


class StartIn(BaseModel):
    subjects: list[str]


@app.get("/api/attempts/active")
def get_active(u=Depends(me), s=Depends(db)):
    a = active(s, u.id)
    return payload(a) if a else None


@app.post("/api/exams/start")
def start_exam(b: StartIn, u=Depends(me), s=Depends(db)):
    a = active(s, u.id)
    if a:
        return payload(a)
    subs = [x for x in dict.fromkeys(b.subjects) if x in SLUG and x != "Use of English"]
    if len(subs) != 3:
        raise HTTPException(422, "Pick exactly 3 subjects besides Use of English.")
    qd = []
    for sub in ["Use of English"] + subs:
        for q in get_q(SLUG[sub], 60 if sub == "Use of English" else 40):
            qd.append({**q, "s": sub})
    a = Attempt(user_id=u.id, kind="aloc", title="JAMB practice", qdata=qd, answers={},
                deadline=time.time() + EXAM_MIN * 60)
    s.add(a); s.commit()
    return payload(a)


class AnsIn(BaseModel):
    answers: dict = {}


def own(s, u, aid):
    a = s.query(Attempt).filter_by(id=aid).with_for_update().first()
    if not a or a.user_id != u.id:
        raise HTTPException(404, "Attempt not found.")
    return a


@app.put("/api/attempts/{aid}/save")
def save(aid: int, b: AnsIn, u=Depends(me), s=Depends(db)):
    a = own(s, u, aid)
    if not a.submitted and time.time() <= a.deadline + GRACE:
        a.answers = clean_answers(b.answers, len(a.qdata))
        s.commit()
    return {"ok": True}


@app.post("/api/attempts/{aid}/submit")
def submit(aid: int, b: AnsIn, u=Depends(me), s=Depends(db)):
    a = own(s, u, aid)
    if a.submitted:
        return a.result
    late = time.time() > a.deadline + GRACE
    ans = (a.answers or {}) if late else clean_answers(b.answers, len(a.qdata))
    return finalize(s, a, ans, late)


# ---------- teacher: PDF -> test ----------
def extract_objectives(pdf: bytes):
    import subprocess, sys
    from objective_parser import parse_objectives
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "extract_pdf.py")],
            input=pdf, capture_output=True, timeout=25, check=True)
        questions = parse_objectives(result.stdout.decode("utf-8"))
        if any(not q["correctAnswer"] for q in questions):
            raise ValueError("Every question must have an answer. Add an Answer key section or Answer: B after each question.")
        return [{"q": clean(q["text"]), "o": [clean(v) for v in q["options"].values()],
                 "a": list(q["options"]).index(q["correctAnswer"]), "review": False} for q in questions]
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except (subprocess.SubprocessError, UnicodeError):
        raise HTTPException(422, "Use a text PDF, up to 25 pages and 10 MB, without images or diagrams. Scanned PDFs cannot be read. Follow the example format below.")


@app.post("/api/teacher/tests/upload")
def upload(file: UploadFile = File(...), title: str = Form(...), subject: str = Form("General"),
           minutes: int = Form(60), publish: bool = Form(True), u=Depends(teacher), s=Depends(db)):
    data = file.file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024 or not data.startswith(b"%PDF"):
        raise HTTPException(422, "Upload a PDF smaller than 10 MB.")
    qs = extract_objectives(data)
    t = Test(title=title.strip()[:200], subject=subject.strip()[:80] or "General",
             minutes=max(5, min(minutes, 180)), published=False, owner_id=u.id)
    s.add(t); s.flush()
    for i, q in enumerate(qs, 1):
        s.add(TQ(test_id=t.id, num=i, q=q["q"], o=q["o"], a=q["a"], review=q["review"]))
    s.commit()
    return {"id": t.id, "count": len(qs), "flagged": sum(q["review"] for q in qs), "published": False}


@app.get("/api/teacher/tests")
def my_tests(u=Depends(teacher), s=Depends(db)):
    return [{"id": t.id, "title": t.title, "subject": t.subject, "published": t.published,
             "count": s.query(TQ).filter_by(test_id=t.id).count()}
            for t in s.query(Test).filter_by(owner_id=u.id).order_by(Test.id.desc()).all()]


def own_test(s, u, tid):
    t = s.get(Test, tid)
    if not t or t.owner_id != u.id:
        raise HTTPException(404, "Test not found.")
    return t


@app.get("/api/teacher/tests/{tid}")
def test_detail(tid: int, u=Depends(teacher), s=Depends(db)):
    own_test(s, u, tid)
    return [{"id": q.id, "num": q.num, "q": q.q, "o": q.o, "a": q.a, "review": q.review}
            for q in s.query(TQ).filter_by(test_id=tid).order_by(TQ.num).all()]


class AIn(BaseModel):
    a: int


@app.put("/api/teacher/tests/{tid}/questions/{qid}")
def set_answer(tid: int, qid: int, b: AIn, u=Depends(teacher), s=Depends(db)):
    own_test(s, u, tid)
    q = s.get(TQ, qid)
    if not q or q.test_id != tid or not 0 <= b.a < len(q.o):
        raise HTTPException(404, "Question not found.")
    q.a, q.review = b.a, False
    s.commit()
    return {"ok": True}


@app.post("/api/teacher/tests/{tid}/toggle")
def toggle(tid: int, u=Depends(teacher), s=Depends(db)):
    t = own_test(s, u, tid)
    if not t.published:
        questions = s.query(TQ).filter_by(test_id=tid).all()
        if not questions or any(q.review for q in questions):
            raise HTTPException(409, "Review and confirm every flagged answer before publishing.")
    t.published = not t.published
    s.commit()
    return {"published": t.published}


@app.get("/api/teacher/tests/{tid}/results")
def results(tid: int, u=Depends(teacher), s=Depends(db)):
    own_test(s, u, tid)
    rows = s.query(Attempt, User).join(User, User.id == Attempt.user_id) \
        .filter(Attempt.test_id == tid, Attempt.submitted == True).order_by(Attempt.score.desc()).all()  # noqa: E712
    return [{"username": x.username, "phone": x.phone, "score": a.score, "late": a.late} for a, x in rows]


class ResetIn(BaseModel):
    phone: str
    password: str


@app.post("/api/teacher/students/reset")
def reset_student(b: ResetIn, u=Depends(teacher), s=Depends(db)):
    phone = re.sub(r"\s", "", b.phone)
    st = s.query(User).filter_by(phone=phone, role="student").with_for_update().first()
    if not st:
        raise HTTPException(404, "No student account with that phone number.")
    valid_password(b.password)
    st.pw = hp(b.password)
    state = auth_state(s, st.id)
    state.version += 1
    state.must_change = True
    state.reset_expires = time.time() + 24 * 3600
    s.add(AuthAudit(actor_id=u.id, student_id=st.id, action="teacher_reset"))
    s.commit()
    FAILS.pop(phone, None)
    return {"ok": True, "username": st.username, "expires_in_hours": 24}


# ---------- students: teacher tests ----------
@app.get("/api/tests")
def tests(u=Depends(me), s=Depends(db)):
    return [{"id": t.id, "title": t.title, "subject": t.subject, "minutes": t.minutes}
            for t in s.query(Test).filter_by(published=True).order_by(Test.id.desc()).limit(50).all()]


@app.post("/api/tests/{tid}/start")
def start_test(tid: int, u=Depends(me), s=Depends(db)):
    a = active(s, u.id)
    if a:
        return payload(a)
    t = s.get(Test, tid)
    if not t or not t.published:
        raise HTTPException(404, "Test not available.")
    qd = [{"s": t.subject, "q": q.q, "o": q.o, "a": q.a, "sol": "", "img": ""}
          for q in s.query(TQ).filter_by(test_id=tid).order_by(TQ.num).all()]
    a = Attempt(user_id=u.id, kind="test", test_id=tid, title=t.title, qdata=qd, answers={},
                deadline=time.time() + t.minutes * 60)
    s.add(a); s.commit()
    return payload(a)


@app.get("/api/results")
def student_results(u=Depends(me), s=Depends(db)):
    import datetime
    return [{"id": a.id, "title": a.title, "score": a.score,
             "out_of": 400 if a.kind == "aloc" else 100,
             "created_at": datetime.datetime.fromtimestamp(a.created, datetime.timezone.utc).isoformat()}
            for a in s.query(Attempt).filter_by(user_id=u.id, submitted=True).order_by(Attempt.id.desc()).limit(100)]


STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
