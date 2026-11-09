# Known correctness and regression fixes

The owner requested fixing known issues. Plan and implementation review used the repository development and design-review skills; both are self-reviews, with no delegated/independent reviewer. The checkout was clean at 28df175 before work, and fixes are isolated on feature/known-issue-fixes. No dependency, model, native-game or storage-schema change is required.

## Reviewed plan and acceptance

Remove automatic response shortening: even deleting complete sentences can remove a later condition or negation. Use the existing validated repair/fallback within the original two-call/deadline cap. Do not attempt semantic clause classification or another model call. Update tests to assert bounded repair, rather than assert the intentionally removed truncation behavior.

Correct provider auto-selection so an explicit model identity outranks leftover credentials for a different provider; native gemini-/models/gemini names retain their Gemini meaning, while vendor/model OpenRouter IDs remain routed through OpenRouter. Explicit profile paths remain authoritative; no credentials are logged or modified.

Restore the historical rollback script/tests deleted during branch integration. Validate manifest names against connector-owned files, source/destination containment and all required source files before backup/restore writes. Keep dry-run, reversible backups, old flat backup compatibility, CLI game-closed guard and public installation guidance. Tests use disposable game-shaped directories only; no installed game is touched.

Fix protected Windows-style console paths on Linux by checking original and resolved path spellings with platform-neutral normalization and directory boundaries. Preserve similarly named unprotected folders. Resolve plugin filenames case-insensitively on a cache miss so the same canonical plugin identity works on case-sensitive hosts. Retain missing/corrupt-plugin behavior and one parsed cache entry.

Also align CV_RESULT's diagnostic status with the full session/revision/TTL acceptance predicate, so an expired descriptor cannot be logged as fresh even though it is discarded correctly.

Acceptance: reproduce wrong provider/shortening behavior and existing four failing tests before fixes; focused component tests; rollback restore/remove/undo/dry-run/missing/traversal/symlink cases; full offline regression; source compilation, dependency/whitespace and changed-document link checks. No paid/live game actions, pushes or commits are part of this task.

## Reproduction and implementation self-review

Before fixes, the reproducing selection/permission tests plus existing path/voice tests produced **7 failures, 47 passes**. Two wrong-provider cases, one local-shortening bypass and all four previous regression failures were observed. The missing rollback entry point was directly confirmed in the checkout; historical implementation and tests were recovered from 3996028 rather than inventing a parallel installer.

Removed _shorten, shorten_text and LENGTH_CODES, along with tests for that removed behavior. Replacement runtime regressions require a genuine provider repair and preserve the explicit rejection reasons/call cap. The negative case includes a permission condition after the first two sentences, which must never be turned into an unconditional local prefix.

Auto provider tests cover both credentials together with OpenRouter, Google-through-OpenRouter and native Gemini model names. Existing generic-key and explicit-profile cases still apply. Windows protected-path tests retain all three original failures and add parent traversal and similarly named sibling directories; no platform skip hides the failures. The voice resolver retains the original failing case/cache test.

Rollback review found the historical implementation could begin copying before discovering a missing later source. All source/destination checks now occur before snapshot or mutation, and tests verify no new backups/writes on this failure. Manifest allowlists, disjoint/unique bounded lists and source containment prevent an arbitrary manifest path or external symlink source from being applied. Dry-run never invokes live game changes. This is preflighted restoration, not a claim of crash-atomic multi-file filesystem transactions. Legacy backups without added-file metadata restore only the known files they actually saved.

CV logging now shares the exact freshness boolean with descriptor admission; the stalled-CV test asserts both discarding and stale_discarded logging at TTL expiry.

## Verification

Initial focused fixes: **125 passed**. Full offline regression before the final logging regression: **994 passed, 6 skipped**, resolving all four previously failing tests. Final results follow below. The six existing skips require Windows capture/known-folder APIs or the absent Mantella.esp reference; no failing test was replaced by a skip.

Native Windows/game playback, real microphone recognition, GPU throughput and live-model semantic quality remain unmeasured. No actual rollback was performed against the owner's game. The runtime remains subject to the documented semantic grounding limitations; eliminating deterministic truncation does not prove model repair preserves every meaning.

Final verification: `.venv/bin/python -m pytest -q --tb=short` — **994 passed, 6 skipped** in 28.84 seconds, no failures. The six warnings are the existing Starlette deprecation and intentionally exercised CPU/unknown-device reporting. `compileall` for src/npc and scripts, `pip check`, `git diff --check`, changed documentation's local links and the restored rollback CLI `--help` all passed. No actual game installation or rollback was performed. Self-review verdict: the identified code/regression issues are resolved under offline coverage; live Windows/GPU/game verification remains a separate gate.
