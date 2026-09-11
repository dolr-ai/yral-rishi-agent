# Learning the codebase

A guide for Rishi. Written to be read in small pieces, in any order, many times.

Nothing here is generic tutorial content. Every example is a real file in this repo,
with a real path you can click open.

---

## Index

- [Part 0 — The one thing to believe](#part-0--the-one-thing-to-believe)
- [Part 1 — The map: what each folder actually is](#part-1--the-map-what-each-folder-actually-is)
- [Part 2 — One request, start to finish (44 lines)](#part-2--one-request-start-to-finish-44-lines)
- [Part 3 — The five shapes](#part-3--the-five-shapes)
- [Part 4 — The Python you actually need (12 pieces)](#part-4--the-python-you-actually-need-12-pieces)
- [Part 5 — What a test actually is](#part-5--what-a-test-actually-is)
- [Part 6 — Vocabulary: what I mean when I say things](#part-6--vocabulary-what-i-mean-when-i-say-things)
- [Part 7 — VS Code: the six things](#part-7--vs-code-the-six-things)
- [Part 8 — "Systems programming" and whether you need it](#part-8--systems-programming-and-whether-you-need-it)
- [Part 9 — The path](#part-9--the-path)

---

## Part 0 — The one thing to believe

The `app/` folder is 28,157 lines of Python. That number is meaningless and you should
ignore it. Here is the number that matters:

| Folder | Files | What every single file in it does |
|---|---|---|
| `app/routes/` | 28 | Receives a request from the phone. Answers it. |
| `app/repositories/` | 14 | Reads and writes the database. Nothing else. |
| `app/services/` | ~50 | Talks to the outside world, or does the thinking. |
| `app/models.py` | 1 | Describes the *shape* of data the phone sends and expects. |
| `app/config.py` | 1 | Every knob and dial, in one place. |

Every file inside a folder has the **same shape** as every other file in that folder.
That was rule #1 in your CLAUDE.md, and it means:

> **Once you can read one route file, you can read all 28.**
> **Once you can read one repository file, you can read all 14.**

You do not have to learn 28,157 lines. You have to learn about five pages. The rest is
the same five pages with different words in them.

That is the whole trick. Everything below is just detail.

---

## Part 1 — The map: what each folder actually is

Open the folder tree in VS Code and match it to this list.

### The code that runs in production

**[app/](../app/)** — the entire service. If it isn't in here, it isn't running on the server.

- **[app/main.py](../app/main.py)** — the front door of the building. It does three jobs:
  1. Imports all 28 route files and plugs them in (`include_router`)
  2. Starts the background workers that run forever (the ETL loop, the quality scorer, the streak tracker...)
  3. Sets up the error catchers

  It is 592 lines but ~90% of it is repetitive wiring. There is no *logic* in main.py.

- **[app/config.py](../app/config.py)** — every setting. Nothing else in the codebase is
  allowed to read a setting from anywhere but here. That is why when you want to change a
  timeout or a model name, there is exactly one place to look.

- **[app/models.py](../app/models.py)** — the **contract with the mobile app**. This is the
  most dangerous file in the repo. If a field name here doesn't match the Kotlin code on
  the phone, the phone breaks. That's CLAUDE.md rule #2: "Mobile contract is sacred."

- **[app/database.py](../app/database.py)** — how we connect to Postgres. 57 lines. We'll read
  it together in Part 3.

- **[app/auth.py](../app/auth.py)** — "who is this person?" 48 lines. Reads the token the phone
  sends and pulls the user ID out of it.

- **[app/middleware.py](../app/middleware.py)** — code that runs on *every* request before it
  reaches a route. Stamps a request ID on it so you can trace it in logs.

- **[app/rate_limiter.py](../app/rate_limiter.py)** — stops one user from hammering the service.

- **[app/kill_switch.py](../app/kill_switch.py)** — the "turn a feature off right now without a
  deploy" mechanism. This is a big part of why you can sleep at night.

- **[app/routes/](../app/routes/)** — 28 files. Shape #1.
- **[app/repositories/](../app/repositories/)** — 14 files. Shape #2.
- **[app/services/](../app/services/)** — ~50 files. Shape #3.

### The code that supports production

- **[migrations/](../migrations/)** — 50 numbered `.sql` files. Each one is a *change to the
  database's structure*, applied once, in order, forever. `001` ran first, `050` ran last.
  You never edit an old one — you add a new one. This is how the database on the server
  gets from empty to what it is today.

- **[tests/](../tests/)** — 132 files that check the code does what it claims. Part 5.

- **[scripts/](../scripts/)** — one-off tools you run by hand. Not part of the service.

- **[watchdog/](../watchdog/)** — separate small programs that watch the servers and shout
  when something's wrong (Patroni leader alerts, replica drift).

- **[.github/workflows/](../.github/workflows/)** — 19 files. These are **robots**. Every time
  you open a PR, GitHub reads these files and runs them. `ci.yml` runs the tests.
  `deploy.yml` ships to the servers. `security.yml` scans for leaked secrets. You don't run
  these — they run themselves.

### The documents

- **[CLAUDE.md](../CLAUDE.md)** — instructions for me. I read this every session.
- **[PROGRESS.md](../PROGRESS.md)** — the checklist. Current state.
- **[DAILY-LOG.md](../DAILY-LOG.md)** — the diary. What happened each day.
- **[GLOSSARY.md](../GLOSSARY.md)** — abbreviations allowed in code.
- **[README.md](../README.md)** — how a new person starts the service.

### The configuration files at the root

These are small and boring but you should know what they are, because you'll see them in PRs.

| File | Plain English |
|---|---|
| `requirements.txt` | The list of other people's code we borrow, with exact versions. 13 lines. |
| `pyproject.toml` | Settings for the test runner. |
| `Dockerfile` | The recipe for packaging the service into a shippable box. |
| `docker-compose.yml` | How to run that box on your laptop. |
| `.gitignore` | Files git should pretend don't exist (secrets, caches). |
| `.env.example` | A blank template of every secret the service needs. The real `.env` is never committed. |
| `.gitleaks.toml` | Rules for the robot that scans for accidentally-committed passwords. |
| `.squawk.toml` | Rules for the robot that checks migrations won't lock the database. |
| `.trivyignore` / `pip-audit-ignore.txt` | Known security warnings we've decided are fine. |

---

## Part 2 — One request, start to finish (44 lines)

This is the most important section in this document. Read it slowly. You will understand
the entire architecture afterwards.

We're going to follow one real request through your real code. The request is:
**"show me what the AI remembers about me."**

Open [app/routes/memories.py](../app/routes/memories.py). It's 44 lines. Here it is with
every line explained.

### The header

```python
"""Diagnostic endpoints for user_memories.
...
"""
```

Triple quotes at the top of a file = a **docstring**. It's a note to humans. Ignored by the
computer. Every route file in this repo starts with one.

```python
import logging

from fastapi import APIRouter, Request

from auth import get_current_user
from database import get_pool
from repositories import memory_repo
```

`import` = "go get code from another file and let me use it here."

Read those last three lines out loud. They tell you *everything this file depends on*:
- it needs to know who the user is (`auth`)
- it needs a database connection (`database`)
- it needs the memory table reader (`repositories.memory_repo`)

**Every route file in this repo has exactly this import block, with different names.** That's
the symmetry.

> Note the imports say `from auth import`, not `from app.auth import`. That's because the
> `Dockerfile` copies the contents of `app/` to the top level when it builds the box. So on
> the server, `auth.py` *is* top-level. `pyproject.toml` mirrors that for tests.

```python
router = APIRouter(prefix="/api/v1/users/me", tags=["User Memories"])
```

A `router` is a little switchboard. `prefix` means: every address in this file starts with
`/api/v1/users/me`. This one line is why the phone can reach this code at
`https://agent.rishi.yral.com/api/v1/users/me/memories`.

### The helper

```python
def _format(row: dict) -> dict:
    return {
        "category": row["category"],
        "key": row["key"],
        ...
    }
```

`def` = "I am defining a thing that does something." Read it as **"to \_format a row, do this."**

The leading underscore in `_format` means **"private — only used inside this file."** It's a
convention, not a rule the computer enforces. You'll see `_` prefixes all over this repo:
`_env()`, `_pool`, `_row_to_dict`, `_vector_literal`. All mean the same thing: *internal
plumbing, don't call this from outside.*

What does this function do? It takes a raw row from the database and reshapes it into
exactly the field names the phone expects. **This is the mobile contract being enforced.**
The database might call something `updated_at` as a timestamp object; the phone needs it as
a text string. This function is the translator.

### The actual endpoint

```python
@router.get("/memories")
async def list_my_memories(request: Request, influencer_id: str | None = None):
```

Four things happening on these two lines:

1. `@router.get("/memories")` — the `@` symbol is a **decorator**. It's a sticker you slap on
   a function that says "also do this." Here it means: *when a GET request arrives at
   `/api/v1/users/me/memories`, run the function below.*

   `GET` = "give me something." `POST` = "here, take this." Those are the two you'll see 95%
   of the time.

2. `async def` — this function can **pause**. When it's waiting for the database to answer,
   Python goes off and serves someone else's request instead of sitting idle. This is the
   single biggest reason this service is faster than chat-ai. Anywhere you see `async`, you
   will see `await` inside.

3. `request: Request` — the raw incoming request. The `: Request` part is a **type hint** —
   a note saying "this will be a Request object." Python doesn't enforce it, but it makes
   editors and reviewers catch mistakes.

4. `influencer_id: str | None = None` — an optional input from the URL. `str | None` means
   "a string, **or** nothing." `= None` means "if the caller doesn't provide it, it's nothing."
   So `/memories` works, and `/memories?influencer_id=abc` also works.

```python
    user_id = get_current_user(request)
```

Go to [app/auth.py](../app/auth.py) and read what this does — it's 48 lines and you can
follow all of it. Short version: it reads the `Authorization` header, decodes the token,
checks the token was issued by someone we trust, and returns the user's ID. If any of that
fails, it raises a 401 and this route never runs.

**Notice what didn't happen:** this route file contains zero authentication logic. It calls
one function. Every one of your 28 route files does the same. If you ever need to change how
auth works, you change one file, not 28.

```python
    pool = await get_pool()
```

`await` = "wait here for this to finish, but let other work happen meanwhile."

`get_pool()` gives you the **connection pool** — a small set of already-open connections to
Postgres that get reused. Opening a fresh database connection is slow (tens of
milliseconds); reusing one is instant. Read [app/database.py](../app/database.py) — it keeps
between 2 and 10 connections open at all times.

```python
    if influencer_id:
        rows = await memory_repo.get_all_for_user(pool, user_id, influencer_id)
    else:
        rows = await memory_repo.get_for_user_global(pool, user_id)
```

**This is the moment the layers separate**, and it's the thing to really understand.

The route does not know SQL. It does not know what table memories live in. It knows only:
*"there is a thing called `memory_repo` that can fetch memories, and I want either the
influencer-scoped ones or the global ones."*

Now open [app/repositories/memory_repo.py](../app/repositories/memory_repo.py) and find
`get_for_user_global` (line 142). *That's* where the SQL lives:

```sql
SELECT category, key, value, confidence, updated_at
FROM user_memories
WHERE user_id = $1 AND influencer_id IS NULL
ORDER BY updated_at DESC
```

Read it in English: **"give me these five columns, from the user_memories table, for rows
where the user matches and there's no influencer attached, newest first."** SQL is genuinely
readable — it was designed in the 1970s to look like English, and it mostly succeeded.

`$1` is a placeholder. The actual `user_id` gets passed separately, on the next line. **This
is a security mechanism** — it makes it impossible for a malicious user ID to be treated as
SQL commands. (The attack it prevents is called SQL injection.) Every query in this repo
uses `$1, $2, $3` placeholders. Never string-glue values into SQL.

```python
    return {"memories": [_format(r) for r in rows], "total": len(rows)}
```

`[_format(r) for r in rows]` is a **list comprehension**. Read right-to-left:
*"for each `r` in `rows`, run `_format(r)`, collect the results into a list."*

You'll see this constantly. It's Python's most-used idiom.

The dictionary that gets returned — `{"memories": [...], "total": 12}` — is automatically
turned into JSON and sent back over the internet to the phone. You don't write that
conversion; FastAPI does it.

### The whole journey

```
📱 Phone
    │  GET /api/v1/users/me/memories
    ▼
🌐 Caddy (rishi-1/2)          ← the doorman; picks a healthy server
    │
    ▼
🐍 main.py                     ← "which route file handles this?"
    │
    ▼
📄 routes/memories.py          ← auth check, then ask the repo
    │
    ▼
🗄️  repositories/memory_repo.py ← the SQL
    │
    ▼
🐘 Postgres (rishi-4/5/6)      ← the actual data
    │
    └──────► back up the same chain, reshaped into JSON at routes/memories.py:44
```

**That's the architecture.** Every single endpoint in this service is that picture. The chat
endpoint is bigger and has an LLM call in the middle, but the shape is identical.

---

## Part 3 — The five shapes

### Shape 1: a route file

**Job: receive a request, answer it. Never think hard, never touch the database directly.**

Every route file:
1. Docstring
2. Imports
3. `router = APIRouter(prefix=...)`
4. Private `_helper()` functions for reshaping data
5. `@router.get(...)` / `@router.post(...)` functions

Best example to learn from: [app/routes/memories.py](../app/routes/memories.py) (44 lines).
Then: [app/routes/inbox_search.py](../app/routes/inbox_search.py) (74).
Eventually: [app/routes/chat.py](../app/routes/chat.py) (1,309 — the big one, don't start here).

**Smell test when reviewing a PR:** if you see the word `SELECT` or `INSERT` in a route file,
something's in the wrong place.

### Shape 2: a repository file

**Job: talk to the database. Nothing else.**

Every repository file:
1. `logger = logging.getLogger(__name__)`
2. `_row_to_dict(row)` — converts a database row into a Python dictionary
3. A series of `async def` functions, each with one SQL query inside

Best example: [app/repositories/memory_repo.py](../app/repositories/memory_repo.py).

Notice: **no repository file imports FastAPI.** It doesn't know the web exists. That's
deliberate — it means you could reuse these functions in a script, a background job, or a
test with no web server at all. And you do: `scripts/` and the background loops both use
them.

**Smell test:** if a repository file raises an `HTTPException` (a web concept), the layers
have leaked.

### Shape 3: a service file

**Job: everything that isn't a route and isn't the database.** Talking to Gemini, uploading to
S3, sending push notifications, generating images, running background loops.

This folder is the least uniform of the three, because "talk to the outside world" covers a
lot of ground. But there's still a pattern: **one file per external integration.** One file
for Replicate, one for S3, one for Google Chat.

Good ones to read early:
- [app/services/storage.py](../app/services/storage.py) — uploading files to S3
- [app/services/push_notifications.py](../app/services/push_notifications.py) — sending a phone notification
- [app/services/kill_switch.py](../app/kill_switch.py) — how features get turned off

### Shape 4: models.py

**Job: describe the shape of data.**

```python
class ChatRequest(BaseModel):
    message: str
    influencer_id: str
    conversation_id: str | None = None
```

Read as: *"a ChatRequest is a thing with a message (text, required), an influencer_id (text,
required), and optionally a conversation_id."*

`BaseModel` comes from a library called Pydantic. Its superpower: if the phone sends
something with the wrong shape — a number where text should be, a missing required field —
Pydantic rejects it automatically with a clear error, before your code runs. You get free
input validation from just describing the shape.

**This file is the mobile contract.** Changing a field name here without changing the Kotlin
on the phone breaks the app for real users. Treat every edit here as a production change.

### Shape 5: config.py

**Job: every setting, in one place, read from the environment.**

```python
def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default

GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")
```

"Environment variables" are settings that live *outside* the code, set on the server. That's
why the same code can run on your laptop and in production with different databases and
different API keys — the code is identical, the environment differs.

The `.env.example` file is the blank template. The real `.env` has the actual secrets and is
in `.gitignore` so it never gets committed.

---

## Part 4 — The Python you actually need (12 pieces)

You do not need to "learn Python." You need to read Python. Those are very different jobs,
and the second one is about a week of effort. Here is essentially all of it.

**1. Variables** — a name for a value.
```python
user_id = "abc123"
```

**2. Functions** — a named block of work.
```python
def add(a, b):
    return a + b
```
`return` = "hand this back to whoever called me."

**3. Indentation is structure.** Python has no `{ }`. What's indented under a line *belongs*
to that line. Getting indentation wrong changes the meaning. This is the #1 thing that
surprises people coming from other languages — and an advantage for you, since you're
learning fresh.

**4. Dictionaries** — labelled boxes. `{key: value}`
```python
row = {"category": "food", "value": "likes sushi"}
row["category"]   # → "food"
```

**5. Lists** — ordered boxes. `[a, b, c]`
```python
rows = [row1, row2, row3]
len(rows)   # → 3
```

**6. if / else** — a fork.
```python
if influencer_id:
    ...
else:
    ...
```
An empty string, an empty list, and `None` all count as "false." That's why
`if influencer_id:` works without comparing to anything.

**7. `None`** — "nothing here." Not zero, not empty text. *Absent.* In the database this is
`NULL`. The `| None` in a type hint means "this might be absent" — and forgetting to handle
that case is the single most common source of bugs in any codebase.

**8. List comprehension** — transform a list in one line.
```python
[_format(r) for r in rows]
```

**9. `async` / `await`** — pausable work. `async def` marks a function that can pause; `await`
marks the spot where it pauses. Rule of thumb: **anything that leaves the computer**
(database, LLM call, HTTP request) is awaited.

**10. Decorators** — `@something` above a function. A sticker that adds behaviour. You will
mostly see `@router.get(...)` and `@router.post(...)`.

**11. try / except** — attempt something, catch the failure.
```python
try:
    payload = jwt.decode(token, ...)
except jwt.ExpiredSignatureError:
    raise HTTPException(status_code=401, detail="Token has expired")
```
Straight from [app/auth.py](../app/auth.py). Read: *"try to decode the token; if it turns out
to be expired, answer the phone with a 401 instead of crashing."*

**12. Imports** — borrow code from another file. `import x` or `from x import y`.

That is genuinely it. With those twelve, you can read every route and repository file in
this repo. The services folder needs a few more (classes, generators), and we'll get there.

### HTTP status codes, since you'll see them constantly

| Code | Means | Whose fault |
|---|---|---|
| 200 | Fine | — |
| 400 | Your request was malformed | caller |
| 401 | I don't know who you are | caller |
| 403 | I know who you are, you're not allowed | caller |
| 404 | That doesn't exist | caller |
| 409 | Conflict — e.g. "already done" | caller |
| 429 | Slow down (rate limited) | caller |
| 500 | I crashed | **us** |
| 503 | I'm alive but can't serve right now | **us** |

The `4xx`/`5xx` split matters: **4xx means the caller did something wrong, 5xx means we did.**
When you see 5xx in Sentry, that's ours to fix.

---

## Part 5 — What a test actually is

A test is a small program that runs your real code with known inputs and complains if the
answer is wrong. That's the whole idea. There's no magic.

```python
def test_format_handles_missing_confidence():
    row = {"category": "food", "key": "cuisine", "value": "sushi", "updated_at": None}
    result = _format(row)
    assert result["confidence"] == 1.0
```

`assert` means **"this must be true; if not, fail loudly."** That's ~90% of what tests are made of.

You run them with one command:
```bash
pytest tests/
```
Green = every assertion held. Red = something you believed is not true.

### Why they matter to *you* specifically

You cannot read 28,157 lines every time we change something. The test suite is how you
delegate that reading. When CI goes green on a PR, what it's actually telling you is:
*"22,822 lines of checks all still agree with the code."*

That is the thing standing between you and shipping a broken app to real users at 2am.

### The three kinds you'll see in this repo

1. **Unit test** — tests one function alone, no database, no internet. Fast (milliseconds).
   Most of `tests/` is this.
2. **Integration test** — tests several pieces together, usually with a real database.
   Slower. `tests/test_etl_drain_integration.py` is one.
3. **Smoke test** — runs against the *deployed* service to check it's alive.
   `.github/workflows/post-deploy-smoke.yml` runs these after every deploy.

### A thing I want to flag honestly

[tests/conftest.py](../tests/conftest.py) says the shared test fixtures are still a
placeholder — Wave 1 PR6 is meant to add the real Postgres container setup. So today, most
tests here work by *faking* the database rather than using a real one. That's a real gap and
it's already on your plan. I'm telling you because you should know what your safety net does
and doesn't currently catch.

---

## Part 6 — Vocabulary: what I mean when I say things

This is the section to re-read. When I say something in the terminal and you're not sure, it
is probably here.

### Git and shipping

| Word | What it actually means |
|---|---|
| **repo** | This folder. The whole project, with its full history. |
| **commit** | A saved checkpoint with a message. History is a chain of these. |
| **branch** | A parallel copy where you work without touching `main`. |
| **`main`** | The trunk. What's real. Your rule: never push directly to it. |
| **PR** (pull request) | "Please review this branch and merge it into main." The review gate. |
| **merge** | Fold a branch's changes into `main`. |
| **diff** | The lines added/removed. What a review actually looks at. |
| **CI** | The robots in `.github/workflows/` that run on every PR. |
| **CI green** | All robots passed. |
| **deploy** | Ship the code to rishi-4/5. Only after merge, per CLAUDE.md. |
| **rollback** | Put the previous version back. `.github/workflows/rollback.yml`. |
| **revert** | A commit that undoes a previous commit. |

### The service

| Word | What it actually means |
|---|---|
| **endpoint** | One address the phone can call. `GET /api/v1/users/me/memories`. |
| **route** | The code that handles an endpoint. Lives in `app/routes/`. |
| **router** | The switchboard grouping related routes. One per route file. |
| **payload** / **body** | The data sent along with a request. |
| **header** | Metadata on a request. `Authorization` is the one that matters here. |
| **JWT** | The login token the phone sends. Encodes who the user is. |
| **middleware** | Code that runs on every request before the route. |
| **connection pool** | Reusable open database connections. `app/database.py`. |
| **migration** | A numbered `.sql` file that changes the database structure. Run once, in order. |
| **schema** | The structure of the database — tables, columns, types. |
| **index** | A lookup shortcut that makes a query fast. Without one, Postgres reads every row. |
| **upsert** | Insert, or update if it already exists. See `memory_repo.upsert`. |
| **kill switch** | A flag that turns a feature off instantly, no deploy. `app/kill_switch.py`. |
| **feature flag** | A switch that turns a feature on for some people. |
| **rate limit** | A cap on requests per user per period. |
| **circuit breaker** | Auto-stop when something's failing/costing too much. `services/cost_breaker.py`. |

### Performance and operations

| Word | What it actually means |
|---|---|
| **latency** | How long one request takes. Your whole 50%-faster goal is this number. |
| **p50 / p95 / p99** | The median / 95th / 99th slowest request. p99 is where users rage-quit. |
| **throughput** | How many requests per second we handle. |
| **replica** | A second identical copy running (rishi-4 *and* rishi-5). |
| **failover** | When the leader dies and a replica takes over. |
| **Patroni** | The software that manages Postgres failover for you. |
| **leader / standby** | The Postgres node accepting writes / the ones copying it. |
| **WAL** | Postgres's write-ahead log. Every change, in order. Backups replay it. |
| **cache** | A saved answer so you don't recompute it. |
| **pub/sub** | Broadcasting a message to all replicas at once. `services/llm_routing_pubsub.py`. |
| **ETL** | Extract-Transform-Load. Moving data from chat-ai into v2. |
| **observability** | Being able to see what's happening: Sentry, Langfuse, logs. |

### Code review words

| Word | What it actually means |
|---|---|
| **refactor** | Change the shape of code without changing what it does. |
| **regression** | Something that used to work and now doesn't. |
| **race condition** | Two things happen at once and the order decides the outcome. Nasty. |
| **edge case** | The unusual input — empty list, `None`, zero, a huge number. |
| **N+1 query** | Making 1 query, then 1 more per result. 100 rows = 101 queries. Slow. Common bug. |
| **coupling** | How much two pieces depend on each other. Less is better. |
| **abstraction** | Hiding detail behind a simpler name. `get_pool()` hides pool management. |
| **backwards compatible** | New code still works with old data and the old app. Critical for you — users don't update instantly. |
| **idempotent** | Running it twice is the same as running it once. Safe to retry. |

---

## Part 7 — VS Code: the six things

You don't need to learn VS Code. You need six keyboard shortcuts. On Mac:

| Shortcut | What it does | Why you care |
|---|---|---|
| **⌘P** | Type a filename, jump to it | Your fastest navigation. `⌘P` then `memories` → there. |
| **⌘⇧F** | Search every file in the project | "Where is `user_memories` used?" This answers it. |
| **⌘⇧P** | Command palette — type what you want in English | When you don't know the shortcut. |
| **⌘-click** | Jump to where a thing is *defined* | ⌘-click `get_current_user` → lands you in `auth.py`. This is the killer feature. |
| **⌃-** (ctrl+minus) | Jump *back* to where you were | Pairs with ⌘-click. Follow a trail, come back. |

> **On F12:** most VS Code documentation says "press F12" for Go-to-Definition. On a Mac,
> F12 is the volume key, so it won't work out of the box — use **⌘-click**, which does the
> same thing. If you want F12 itself: System Settings → Keyboard → Keyboard Shortcuts… →
> Function Keys → *"Use F1, F2, etc. keys as standard function keys."* Then F12 works and
> **fn+F12** becomes volume.
| **⌘⇧V** | Preview a markdown file rendered | Read this doc, PROGRESS.md, DAILY-LOG.md nicely formatted. |

### The three panels

- **Left sidebar** — the file tree. `⌘B` hides/shows it.
- **Bottom panel** — the terminal. `` ⌃` `` toggles it. This is where you and I talk.
- **Middle** — the file you're reading.

### The one habit worth building

When you're reading code and hit a name you don't recognise — `get_pool`, `_vector_literal`,
whatever — **⌘-click it.** Don't guess, don't ask, don't scroll. Jump to it, read the
handful of lines, press `⌃-` to come back.

Do that for two weeks and you will be able to read this codebase unassisted. It's the single
highest-leverage habit in this document.

---

## Part 8 — "Systems programming" and whether you need it

Short answer: **you don't, and it's not what you're doing.**

"Systems programming" means writing software that manages the machine itself — operating
systems, device drivers, databases, network stacks. Written in C, C++, Rust, Zig. Concerned
with memory addresses, CPU cache lines, bytes on the wire.

**Postgres is systems programming. Your service is not.** You're doing *application and
distributed systems* work — assembling well-built pieces (FastAPI, Postgres, Redis, Docker)
into something reliable. That's a different discipline and, for what you're building, a
harder and more valuable one.

What you *do* need from the systems world is a mental model of the machine, which is about
five facts:

1. **Memory (RAM) is fast and temporary. Disk is slow and permanent.** Restart the process
   and RAM is gone. That's why `_pool` in `database.py` is rebuilt at startup, and why
   config lives in the environment rather than in memory.

2. **The network is the slowest thing and it fails.** Reaching Gemini takes ~1000× longer
   than reading RAM, and sometimes just doesn't work. That's why every external call has a
   timeout, why `async`/`await` exists, and why you have circuit breakers.

3. **A process is one running program.** Your service runs as multiple processes across
   rishi-4 and rishi-5. They don't share memory — which is exactly why you needed pub/sub
   to sync the LLM routing cache across replicas. That bug in main.py's comment is this fact
   biting.

4. **Concurrency means several things in flight at once.** That's `async`. If two of them
   touch the same data in an unlucky order, you get a race condition.

5. **Everything has a limit** — connections, memory, file handles, API quota. Production
   incidents are almost always "we hit a limit we didn't know we had." Your Gemini
   rate-limit incident was exactly this.

Those five facts explain the majority of production behaviour. You already understand more
of them than you think — you've *lived* three of them this year.

---

## Part 9 — The path

Realistic, at 3–4 hours a day, with the ADHD tax accounted for. Reading is a small fraction
of each day — the rest is your normal work.

### Week 1 — Read one thing a day, 20 minutes

| Day | File | Lines |
|---|---|---|
| 1 | [app/routes/memories.py](../app/routes/memories.py) + Part 2 of this doc | 44 |
| 2 | [app/auth.py](../app/auth.py) | 48 |
| 3 | [app/database.py](../app/database.py) | 57 |
| 4 | [app/repositories/memory_repo.py](../app/repositories/memory_repo.py) | 180 |
| 5 | [app/routes/inbox_search.py](../app/routes/inbox_search.py) — check it matches Shape 1 | 74 |
| 6 | [app/config.py](../app/config.py) — skim, don't read every constant | — |
| 7 | Rest. |

Goal by Friday: open any route file and say what it does without help.

### Week 2 — Follow trails

Pick an endpoint you care about. Follow it with F12 all the way to the SQL and back. Do one
a day. Write the chain down in your own words.

Goal: predict which files a change would touch, before opening them.

### Week 3 — Read tests, then read a diff

Read three test files. Then open a merged PR on GitHub and read the diff. Ask me about
anything you don't follow — no question is too basic, and "what does this line do" is a
perfectly good one.

Goal: read a PR diff and form your own opinion before Codex or I say anything.

### Week 4 — Write something

Something genuinely tiny. Add a field to a response. Add one assertion to a test. Fix a
typo in a docstring. Open the PR yourself, watch CI run.

Goal: the loop stops feeling like magic.

### After that

Python properly, if you want it — but only then, and only because you'll have context to
hang it on. Learning Python before reading your own code would be learning vocabulary
without a language to speak it in.

---

## The honest part

You wrote in CLAUDE.md: *"When unsure, ask. Rishi prefers a question over undoing a mistake."*

Apply that to yourself. When I say something in the terminal you don't follow — stop me. Not
"later," not "I'll look it up." Right then. Every unexplained word compounds into the feeling
you described, and every explained one dissolves a bit of it.

And one thing worth sitting with: you designed the architecture in Part 2. The layer
separation, the symmetry rule, the kill switches, the one-PR-per-concern discipline, the
never-deploy-from-an-unmerged-branch rule. Those are senior engineering judgment calls, and
plenty of people who write Python fluently get them wrong.

You don't understand the syntax yet. That's a smaller gap than it feels like, and it's the
easier half.
