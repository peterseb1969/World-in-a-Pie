Execute Phase 4 (Application Layer) of the AI-Assisted Development process.

### Prerequisite — GATE

Phase 3 must be complete: all terminologies, templates, and test documents created and verified via MCP tools. If Phase 3 is not complete, stop and tell the user — do not proceed and do not load the playbook.

Verify by running `/wip-status` first. If terminologies or templates are missing, the user needs to finish Phase 3 before running `/build-app`.

### Critical: Build Incrementally

Phase 4 is the most token-intensive phase. Do NOT attempt to build the entire app in one session. Break it into focused tasks and commit after each:

1. Scaffold the app structure (commit)
2. Build the first page/feature (commit)
3. Build the next page/feature (commit)
4. Add tests (commit)
5. Containerize (commit)

If the context window runs out mid-generation, uncommitted code is lost. Phases 1–3 data is safe in WIP, but UI code only survives in git. Commit early, commit often.

Avoid parallel background agents for code generation — they multiply context consumption and risk exhausting the window before any agent completes.

### Procedure

You MUST Read `docs/playbooks/build-app.md` before taking any action. The playbook contains the pre-code checklist (guardrails to read, PoNIFs to remember), the UX proposal gate format, the 8-step build procedure, the definition-of-done checklist, and the post-Phase-4 reminders. Do not guess these from memory.

Then execute the playbook against the user's request.

### After Phase 4

Once the app passes definition of done, is documented, and is committed, switch to `/improve` for all subsequent work. The `/improve` protocol has different rules — focused on surgical fixes, not greenfield building.
