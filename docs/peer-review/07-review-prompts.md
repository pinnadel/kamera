# 07 — Review prompts (paste into another LLM)

Pre-written prompts a reviewer can paste **directly into another LLM** to get a focused critique on one axis. Each prompt is self-contained: it includes the system context the LLM needs without forcing it to read the whole package.

The prompts assume the reviewer has already attached or pasted the relevant doc files (`01-architecture.md`, etc.). Each prompt names exactly which files it depends on.

---

## Prompt 1 — Architecture review (depth)

**Files to attach:** `00-overview.md`, `01-architecture.md`, `06-known-risks.md`, `endpoints.json`, `schema.sql`.

> You are reviewing a single-user, local-only desktop photo-culling app: a Python/FastAPI backend, a React/Vite frontend, and a SQLite store. The architecture is documented in the attached files.
>
> Your task is **adversarial**: assume the author is *over*-confident in this design and find the load-bearing assumptions that would break under realistic stress.
>
> Specifically, evaluate:
>
> 1. **Process model.** Is the synchronous-FastAPI + per-photo-ThreadPoolExecutor model defensible? What concrete failure modes (OS-level, Python-level, hardware-level) would force a redesign? Where would async + a queue actually pay off?
> 2. **State boundaries.** The frontend keeps everything in `App.jsx` and the backend has three module-level globals (`_progress`, `_stop_event`, `_personal_model`). Is this honest, or is it papering over a need for proper state management?
> 3. **Cancellation semantics.** Stop-analysis is best-effort with a 2–5 s tail. Is "Stopping…" honesty an acceptable trade-off, or is there a cheap way to make cancellation harder?
> 4. **SQLite at scale.** What number of photos (10k? 100k?) breaks this design — and why? Per-photo commits, embedding-as-JSON, no indices on score columns: rank these by severity.
> 5. **The 17-feature personal model.** The author concedes overfit risk. Is the *integration* path (cache, invalidation, persistence) sound enough that a redesign of the model itself wouldn't require rewriting the integration?
>
> Output format:
> - Three "concrete things to change before this scales beyond the author's machine," ranked.
> - One "thing to leave alone — the author got this right."
> - One "thing the author thinks is fine that is actually a future bug" (specifically, an issue NOT in `06-known-risks.md`).
>
> Be specific. Cite file paths and line ranges. Don't summarise the system back at me — assume I wrote it.

---

## Prompt 2 — ML stack review (depth)

**Files to attach:** `02-models.md`, `03-scoring.md`, `features.json`, `06-known-risks.md`.

> You are reviewing the ML / scoring layer of a local photo-culling app. Five Phase-2 models (MediaPipe FaceLandmarker, TOPIQ IQA, LAION Aesthetic Predictor V2, SigLIP base, optional LM Studio LLM) feed a 17-dim feature vector into a Phase-3 GradientBoostingRegressor that predicts a personal-taste delta. Phase 1 is deterministic (tile-p90 sharpness fusion + histogram exposure).
>
> Your task: **stress-test the ML stack as a whole.** I am NOT asking for individual model recommendations. I am asking whether the *composition* of these signals into a culling decision is principled.
>
> Critique these specifically:
>
> 1. **Triple-CLIP risk.** TOPIQ, LAION aesthetic, and SigLIP all have a CLIP-or-CLIP-adjacent training distribution. Is this hidden correlation a problem for the personal model? Are we getting genuine signal diversity or just three views of the same prior?
> 2. **`overall_score` is BOTH a feature AND the additive base for `personal_score = overall_score + Δ × 25`.** The author concedes this is double-counting. Quantify the magnitude: under what training conditions does this become a real ordering problem (not just a theoretical one)?
> 3. **Cliff thresholds in deterministic Phase 1.** The exposure score has hard cutoffs (`|μ−128| > 80` → −40). The sharpness fusion has a magic constant (2.3) calibrated against ~50 of the author's photos. Is this calibration approach defensible for a tool intended for a single user, or does it introduce per-camera drift?
> 4. **The auto-cull mode switch at decision 20.** Below 20: threshold rules. At/above 20: personal model alone. No blending. No "is the model actually informative?" test. What's the right gating policy?
> 5. **Maybe = 0.0 in label mapping.** Treat Maybe as halfway between Keep and Reject. Is this defensible, or should Maybe be `nan` (excluded from training)?
> 6. **No held-out validation.** What's the lightest-weight validation regime that would *actually* tell the author whether the personal model is helping?
>
> Output format:
> - The single biggest design-level problem with the ML composition (not a single-model issue).
> - A 5-line "if this were my project" prescription for the personal model that respects the constraint of ~20–100 training samples.
> - One praise paragraph: what the author got architecturally right that most people get wrong.

---

## Prompt 3 — UX review (depth)

**Files to attach:** `00-overview.md`, `04-ux.md`, `05-decisions-log.md`, `06-known-risks.md`.

> You are reviewing the UX of a local photo-culling app for working photographers. The user is the only user; their workflow is high-volume RAW culling (hundreds of photos per session). The app is dark-themed (Raycast-derived), keyboard-first, with multi-tab folder workspaces.
>
> Your task: **find the friction points the author hasn't named yet.** They've documented the ones they know about. You're looking for the ones they've gone blind to.
>
> Critique:
>
> 1. **The auto-cull modal.** Highest-stakes interaction (one click moves potentially hundreds of files). The preview shows aggregate counts. The author concedes the rule-driven-cliff problem in `06-known-risks.md`. Without changing the rule logic, what UX changes to the preview would catch a misfiring rule before the user clicks Run?
> 2. **The Train mode flow.** Score-blind queue, K/M/R, 1.5 s reveal overlay showing "you overruled the AI," ConflictModal for re-shows. Is "score-blind" psychologically what the author thinks it is, or is the user still anchored to the previous photo's perceived quality?
> 3. **DetailView density.** Sticky header + scrollable body, three section types, color-tinted chips, persisted-per-section open state. Where does the user lose orientation? What's a likely "I clicked the wrong thing because the affordance read as something else"?
> 4. **The tab system.** Per-tab state, single watcher, sequential analysis only. The "Watch live" pulse dot is a clever low-attention indicator. But what fails when the user has 6 tabs open (memory? cognitive load? tab-switch latency)?
> 5. **Keyboard model.** K/M/R + arrows + ⌘Z + H + Tab. Is the binding *load* honest, or is the author at the edge of what a fresh user could learn in one session? What's the worst keyboard collision a Cmd+K palette would cause if added later?
> 6. **The "no hero empty state" decision** (documented in `05-decisions-log.md`). The author rejected a centered marketing-style CTA. Was this right, or is the user now staring at a too-subtle "+ New analysis" tab on first launch?
>
> Output format:
> - The TOP THREE friction points NOT already in `06-known-risks.md § UX`.
> - For each: severity (catastrophic / annoying / nitpick), evidence (which doc paragraph supports it), proposed fix (1-2 sentences, design only — don't redesign the data model).
> - One paragraph: "what makes this UX *good* that I would steal for my own product."

---

## Prompt 4 — Composite review (one-pass)

For a reviewer with limited time who wants a single take across all axes.

**Files to attach:** the entire `docs/peer-review/` directory.

> You are reviewing a personal photo-culling app: Python backend, React frontend, ~12k LOC, single user, no tests. Three review axes: architecture, ML/scoring, UX.
>
> The author has prepared this review package and named their own known risks in `06-known-risks.md`. Do not just rank-order their list; produce something they couldn't write themselves.
>
> Your task in 800 words or less:
>
> 1. **One sentence:** what is this project actually doing well that a less self-aware author would have failed at?
> 2. **The one architectural decision** that is most likely to cause pain in the next 6 months. Why?
> 3. **The one ML decision** that is most likely to be revealed as wrong once the personal model trains on real data.
> 4. **The one UX decision** that's a hidden landmine — works for the author, breaks for any second user.
> 5. **One thing the author should stop worrying about** — listed in known-risks but actually fine.
> 6. **The next move.** Given the project is at version 0.9 and the author is a senior UX designer learning to code, what's the single highest-ROI thing to do next? Pick one of: ship as-is, write tests, train the personal model on real data, refactor `App.jsx` / `main.py`, change a model, or "something else."
>
> Be direct. Skip hedging. Cite file paths. The author has explicitly invited this critique.

---

## Prompt 5 — A specific challenge (use this if you want to push hard on one thing)

**Files to attach:** `02-models.md`, `03-scoring.md`, `features.json`.

> Single question: **The personal model formula is `personal_score = clamp(overall_score + clamp(model.predict(features) × 25, -25, +25), 0, 100)` — and `overall_score` is feature index 3 in the input vector.**
>
> Argue both sides:
>
> A) "This is a clear bug. Drop `overall_score` from the feature vector."
>
> B) "This is fine because the cap at ±25 prevents catastrophic ordering, and the model can learn to predict zero on `overall_score` if it isn't useful."
>
> Then take a side and defend it. ~300 words.

---

## Notes for the reviewer

- These prompts are designed for a model with strong general engineering judgment (Claude Opus 4.x, GPT-5, Gemini 2.5 Pro tier). A smaller model will produce surface-level outputs.
- If you want to use prompt 1–3 as a *batch*, run them in parallel sessions — don't concatenate. Each prompt is intentionally framed to make the model take an opinionated stance, and concatenation will dilute that.
- The author welcomes critique that contradicts the package itself. If you find this package is missing context that a real reviewer needs, that's also a finding.
