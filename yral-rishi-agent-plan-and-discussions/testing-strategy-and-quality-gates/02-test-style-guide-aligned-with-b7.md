# Test Style Guide (aligned with B7 doc standard)

> Tests are code. Per B7, every code file follows the 3-tier reading flow. Tests included. **Goal: Rishi can scan test names + docstrings to know what's covered without reading test code.**

## Test naming — plain English sentences

Pattern: `def test_<actor>_<does_what>_<under_what_condition>():`

```python
   GOOD ✅
   def test_user_can_send_message_when_within_paywall_limit():
       ...
   
   def test_orchestrator_returns_billing_error_when_no_access():
       ...
   
   def test_etl_preserves_every_chat_ai_message_id():
       ...

   BAD ❌ (cryptic, abbreviated, restates code)
   def test_msg_send_ok():
       ...
   
   def test_billing_check():
       ...
   
   def test_etl_001():
       ...
```

CI lint (per B1/B5) blocks PRs with cryptic test names.

## Test docstring — the WHY

Per B7 function-header rule, every test gets a 1-3 line docstring:

```python
def test_user_can_send_message_when_within_paywall_limit():
    """
    WHAT: User has 30 free messages used, sends message #31.
    WHEN: Pre-chat billing check returns hasAccess=true.
    WHY:  Paywall threshold is 50 (per E7); 31 should succeed.
    """
    ...
```

## Test file header

Every test file gets a B7-style header explaining what it covers:

```python
"""
╔══════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║  FILE: tests/unit/test_billing_pre_check.py                           ║
║                                                                        ║
║  ⭐ THIS FILE IN ONE SENTENCE                                          ║
║  Verifies the billing pre-check middleware correctly decides whether  ║
║  a user can send a chat message based on yral-billing's response.     ║
║                                                                        ║
║  📖 EXPLAINED FOR A NON-PROGRAMMER                                     ║
║  Before every chat message, our service asks yral-billing "does this  ║
║  user have access?" These tests verify we handle every yral-billing   ║
║  answer correctly: YES (let them send), NO (return paywall response), ║
║  TIMEOUT (fail safely), MALFORMED (fail safely).                      ║
║                                                                        ║
║  🔗 HOW IT FITS                                                        ║
║  - Tests app/billing_check.py                                         ║
║  - Mocks the yral-billing HTTP client                                 ║
║  - Uses the standard pytest-httpx fixture                             ║
║                                                                        ║
║  📥 INPUTS / 📤 OUTPUTS                                                ║
║  - Input: simulated user_id + bot_id + mocked billing response        ║
║  - Output: pass = correct decision; fail = wrong decision or crash    ║
║                                                                        ║
║  ⭐ START HERE                                                         ║
║  Read test_user_can_send_message_when_within_paywall_limit first      ║
║  — it's the happy path. Other tests are edge cases of that.           ║
║                                                                        ║
╚══════════════════════════════════════════════════════════════════════╝
"""
```

Same standard as production code per B7. ADHD-friendly: scan one block per file to know what it covers.

## Test priority order in file

Per B7, functions in PRIORITY order — most important test FIRST:

```python
# 1. Happy path (the most-likely scenario)
def test_user_can_send_message_when_within_paywall_limit(): ...

# 2. Common edge cases
def test_user_blocked_when_at_paywall_limit_without_access(): ...
def test_user_can_send_message_when_paid_access_active(): ...

# 3. Error handling
def test_billing_timeout_fails_open_to_paywall_response(): ...
def test_billing_returns_5xx_logs_to_sentry_and_fails_open(): ...

# 4. Rare edge cases
def test_user_with_concurrent_access_grants_uses_latest(): ...
```

NOT alphabetical. NOT random. Reading top-to-bottom = decreasing importance.

## Comments inside tests — same B7 ROLE-not-SYNTAX rule

```python
def test_user_can_send_message_when_within_paywall_limit():
    """WHAT/WHEN/WHY block (above)"""
    
    # Set up: fresh user with 30 messages used (under the 50 threshold)
    user = create_test_user(messages_sent=30)
    
    # Mock yral-billing's response: hasAccess=true, expires in 1hr
    mock_billing_returns_active_access(expires_at=NOW + ONE_HOUR)
    
    # The action under test
    response = client.post("/api/v1/chat/conversations/abc/messages",
                           headers=auth_headers(user))
    
    # The user should successfully send (200), not hit paywall
    assert response.status_code == 200
    assert "messages_sent" in response.json()
```

Comments explain ROLE in the test scenario. Not what `client.post` does syntactically.

## What NOT to test

```
   ❌ Don't test framework code (FastAPI routing, Pydantic validation)
       Trust libraries. Test YOUR logic, not theirs.

   ❌ Don't test pure data classes without behavior
       BotAccess(user_id, bot_id, expires_at) — no logic, no test needed.

   ❌ Don't test private/internal helpers in isolation when they're
       fully exercised through public-API tests
       Coverage from above is fine.

   ❌ Don't write tautological tests
       def test_returns_42(): assert returns_42() == 42  ← Codex flags this.

   ❌ Don't test logging/instrumentation output unless that's the contract
       Sentry/Langfuse calls are fire-and-forget; don't assert on them
       unless the test is specifically about that.
```

## What ALWAYS gets tested

```
   ✅ Branching logic (if/else, switch on enum)
   ✅ Boundary conditions (>=, <=, off-by-one)
   ✅ Error paths (raises, returns error response)
   ✅ External dependency failures (DB down, LLM timeout, billing 5xx)
   ✅ Auth bypasses (does an unauthenticated request fail?)
   ✅ Idempotency (same X-Idempotency-Key twice = same response)
   ✅ Data preservation (ETL: count(input) == count(output))
   ✅ Latency budgets for hot-path methods
```

## How AI agents write tests under this standard

Coordinator prompt to sessions when generating tests:

```
You are writing tests under CONSTRAINTS B7 (doc standard) and J1-J5
(testing strategy). For every test:

1. Name it as a plain English sentence:
   def test_<actor>_<does_what>_<under_what_condition>(): ...

2. Add a WHAT/WHEN/WHY docstring (1-3 lines).

3. Add a B7-style file header at the TOP of the test file.

4. Order tests in the file by priority (happy path first, edge cases
   last).

5. Comment role-not-syntax inside the test body.

6. Cover the WHAT/WHEN/WHY documented in the production code's
   function header. If a function says "WHEN: called by middleware
   on every request," there should be tests for that scenario.

7. Don't write tautological assertions.

8. Don't test framework code or pure data classes.

After writing tests, self-check: would Rishi (non-programmer + ADHD)
understand what each test covers from name + docstring alone?
```

Codex review verifies these.
