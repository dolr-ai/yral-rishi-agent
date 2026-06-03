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
