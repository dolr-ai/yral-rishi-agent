# Mobile Expert — Hard-Won Lessons

**Append-only.** Each new lesson learned the expensive way (false-conclusion turns, wasted builds, regressions caught by Rishi) gets added at the bottom with the date and the bug-trail context that made it expensive. Existing entries are NEVER rewritten — that erases the cost lesson.

**Read this file before starting any mobile work in a fresh session.** The five lessons below cost a multi-hour debugging arc that should not be repeated.

---

## P1 — Multi-flavor Android install hazard

This project has two debug flavors with two different package IDs:
- `com.yral.android.app` ← **prod-debug** (Rishi tests this)
- `com.yral.android` ← staging-debug (separate app icon)

For multiple turns I ran `:androidApp:installStagingDebug`, the install succeeded, but the staging APK landed as a separate app that Rishi never opened. He was testing prod-debug the whole time, which had no probe code in it. **Always run `installProdDebug` for this project, OR confirm which app icon Rishi is opening before drawing conclusions from his observations.** Verify after install by pulling `pm path com.yral.android.app` and grepping the dex for an expected new string.

## P2 — Gradle build cache silently served stale artifacts

Edits hit disk, but `:shared:features:chat:compileDebugKotlinAndroid` came back as `UP-TO-DATE` and later `FROM-CACHE`, so the new code never reached the APK. Causes: (a) Gradle uses content hashes, not mtimes — `touch` does nothing. (b) The build cache has cross-build entries it'll match against. **For any diagnostic build where you must be sure the new code is in the output, use `:shared:features:chat:clean` AND `--no-build-cache --rerun-tasks`.** Confirm with the next point below.

## P3 — macOS `strings` fails silently on JVM `.class` files

Xcode's `strings` errors with "fat file truncated or malformed" and returns 0 lines, which looks identical to "string not present." **For verifying compiled artifacts, use `LC_ALL=C grep -a "marker" file.class` or `unzip -p apk.apk "classes*.dex" | LC_ALL=C grep -ao "marker"`.** This wasted multiple turns where I thought my edits weren't compiled.

## P4 — Claims-vs-code drift

Twice in this session I stated things about the code that the code didn't actually do (claimed cursor wasn't in the content string; claimed Text path was used during streaming for all content). Codex consultant caught both by reading the file. **Re-read the actual code before making any claim about it; if a comment in the code disagrees with what the code does, the comment is wrong, not the code.** The misleading comment in `ConversationMessagesList.kt` was authored by Phase 5b me, and Codex was reading it as authoritative.

## P5 — Don't theorize past validation

I was three rounds deep into "it's a path swap at first token" theories before the probe confirmed the actual cause was Markdown library re-render. Run the probe first.

---

## Phase 5b ↔ 5c contract — invariants to never violate

If a future session touches `RegularBubble`, `MessagesList`, or `startStreamingAssistantReply`:

- **Path lock**: `markdownLockedOverride` pins Markdown vs Text per stream. NEVER let the path switch mid-stream or at done. The `useMarkdown` branch in `RegularBubble` must always honor an explicit override before falling back to `shouldRenderAsMarkdown(content)`.
- **Cursor isolation**: the streaming cursor MUST be a sibling composable, never appended to the content string the renderer parses. If you change cursor presentation (e.g., move from "below the text" to "bottom-end overlay"), keep the input-string isolation property.
- **Coalesce window**: 250ms is the shipped value. Don't tune without re-measuring on a Motorola with realistic Gemini token cadence (the diagnostic logs in the conversation show typical batch sizes 80-180 chars, 3-5 batches per reply).
- **Flush ordering**: `flushJob?.cancelAndJoin()` MUST run before any code that removes the streaming Local (Done, Failed). Otherwise a lingering 250ms flush will append text after the Local has been replaced by the Remote.

## Verified root cause of SSE streaming flicker

**Per-batch mid-stream flicker** = `com.mikepenz:multiplatform-markdown-renderer:0.38.0` re-parses the entire content string on every recomposition. With tokens arriving 3-5 times per reply, the user sees the bubble "refresh" once per network token batch. This was *verified* by a diagnostic probe that forced Text-path during streaming — flicker disappeared, then reappeared once at `done` (the intentional Text→Markdown swap inside the probe).

**Amplifier**: the cursor "▌" was being appended to the string passed to `Markdown(content = ...)`. Every token batch grew the string by `(new token text + 1 trailing cursor char that shifted position)`. Eliminating the trailing cursor character from the parser's input reduces the per-batch re-parse delta.

**Mitigation** = path-lock (Phase 5b, already shipped) + cursor isolation (this session) + 250ms coalescing (this session). Together they reduce streaming bubble re-parses from N (per network token) to roughly N/3 (per coalesced window), with no Markdown/Text path swap anywhere in the stream lifecycle.

---

## P6 — The wire is wired ≠ the wire carries data

**The mistake:** wiring an endpoint, a DTO, a mapper, and a list view all compile and pass. The Explore agent reports back: "the source merely passes `influencerId=null` to list all conversations." The code looks correct at every interface boundary. You ship. The H2H "Send Message" works, but the new conversation never shows up in the inbox.

The root cause was visible only by reading the SQL inside the repository function — an `INNER JOIN ai_influencers` that silently dropped every row where `influencer_id` was NULL. None of the surface-level checks could see it: the function signature said "list all conversations for the user," the mapper handled the case where `influencer` was null, the UI had dual-avatar logic for H2H rows. Every layer told the truth about its own interface. The data flowing between them was filtered upstream by a `JOIN` clause that the interface didn't expose.

**Why this is its own lesson and not just a restatement of P4:** P4 is "the code may disagree with the comment — trust the code." P6 is the next layer down: the code at the interface boundary may also disagree with the *deeper* code that produces the data the interface returns. A function called `list_by_user(user_id)` whose name and signature say "all conversations for user" can still silently drop H2H rows because of how the SQL is written. The compile passes. The types match. The function name is honest about *what it intends to return*. The implementation differs from the intent and nobody catches it because nobody reads three layers down.

**Defense:**

1. **Read the SQL, not the function name.** When a function returns rows from a database, the source of truth is the `SELECT ... FROM ... JOIN ... WHERE ...` block, not the function's docstring. Open the repository file. Read the actual query. Trace each `JOIN` and `WHERE` clause against the data shape you expect to see.

2. **Trace the actual response shape through to the UI render.** Not just the type system — the *values*. For each field the UI displays, follow it: API JSON → DTO field → mapper → domain field → state → composable. If any layer drops the field (or substitutes a default) the layer below will silently render the default and you won't know why.

3. **Run the test plan you wrote — don't skip.** When I write `§H3: H2H conversation shows in the unified inbox with the other user's avatar` in my own plan, that's a test that exists to catch exactly this class of bug. Skipping it because "the data layer compiles" is the same as not writing it. Either delete the test from the plan or run it.

4. **Don't trust Explore agent summaries about data flow.** The agent's job is to map call sites; the agent does NOT verify that the data those call sites carry matches what the call site implies. "Backend query is not hardcoded to AI" was a summary derived from the *call signature*, not the SQL. If a summary makes a claim about data behavior (what rows come back, what fields populate), verify by reading the implementation — never inherit the claim.

**Bug-trail context, 2026-05-30:** Day-9 of the H2H build. I shipped 7 commits across two sessions that wired the H2H create endpoint, the send endpoint, the typed sender_id propagation, and a chat-screen functional flow. End-to-end H2H send worked. Rishi tested by messaging `abledearcorgi` — message sent, conversation created in the database, sender on the receiving end gets a WebSocket push. But the conversation never appeared in Rishi's inbox. My H2H-IMPLEMENTATION-PLAN.md §4.1 had stated "the existing combined inbox `GET /conversations` already surfaces H2H conversations once we add `conversation_type` parsing" with a note "Confirm with backend whether the combined endpoint is canonical." I never confirmed. The SQL in `conversation_repo.list_by_user` did an `INNER JOIN ai_influencers`, which excluded every H2H row. Caught only when Rishi tested the inbox visibility I had originally written down as test case §H3 and never executed.

---

## P7 — Permissive deserialization defaults are load-bearing during backend evolution

**The catch:** when backend PR #228 added an `is_online` field to the AI `influencer` block in the unified inbox response, mobile shipped without any DTO change for that field. The new field did not crash anything because `JsonHelper.kt` configures the Json instance with `ignoreUnknownKeys = true` and `isLenient = true`. Mobile silently dropped the new field, the inbox rendered as before, and nobody noticed until I did the post-deploy code-read pass and explicitly accounted for it.

This is good behavior, not luck — but it depends on a single Json-instance config line at the boundary, and the *value* of that line is invisible to every layer above it. The DTO doesn't know it's missing a field. The mapper doesn't know. The composable doesn't know. The compiler can't help. The only signal that this safety net exists is the absence of the bug.

**The lesson:** permissive deserialization defaults are load-bearing infrastructure during periods when the backend is evolving. If the team ever tightens these defaults — to catch typos, enforce schema strictness, or surface unexpected fields — that change is not a routine config tweak. It is equivalent to a coordinated backend-frontend release: every DTO must be audited against the *current* backend response shape, on the *current* deployed branch, before the strict config can ship without breaking the inbox.

**Concrete properties of the safety net we currently rely on:**

1. `Json { isLenient = true; ignoreUnknownKeys = true }` at `shared/libs/http/src/commonMain/kotlin/com/yral/shared/http/JsonHelper.kt:6-8`. This is the single point where unknown-key tolerance is configured.
2. Every DTO consumed by this `Json` instance inherits that tolerance. Removing the config line silently flips every DTO from forward-compatible to strict.
3. Backend ships new fields routinely (`is_online` is one of several this quarter). Each one is a silent "test" of the tolerance.

**Defense if you ever touch JsonHelper.kt:**

1. **Don't remove `ignoreUnknownKeys = true` without a coordinated audit.** Open every `@Serializable data class` consumed by this Json instance. For each, fetch the current backend response on the staging/dev branch and diff field-by-field. Add fields the backend now sends but the DTO doesn't model.
2. **If you want stricter behavior on a specific DTO, prefer field-level annotations (`@Required`, default values, sealed type validation) over flipping the global config.** Global strict-mode breaks every DTO at once; field-level enforcement breaks only the contract you're tightening.
3. **Treat unknown-key warnings, if you add a custom serializer that surfaces them, as a forward-compat signal not a bug — log them, don't throw.** The backend is allowed to ship new fields; mobile catches up at the next DTO update window.

**Bug-trail context, 2026-05-30:** while doing the code-read pass for the post-PR-#228 inbox retest, I noticed that `ConversationInfluencerDto` does not model `is_online` even though the backend now sends it. The reason the AI rows still parse is `JsonHelper.kt:8`'s `ignoreUnknownKeys = true`. The lesson was visible only because I explicitly went looking for "what does the new field do to my parser?" — a question that comes from P4/P6 discipline, not from the type system or the test suite.
