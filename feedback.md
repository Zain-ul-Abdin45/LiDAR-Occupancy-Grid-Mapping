# Presentation Feedback — LiDAR Occupancy Grid Mapping (Interim)
**Date reviewed:** 2026-05-23  
**Presentation due:** 2026-05-24 (upload) | **Live presentation:** 2026-06-01

---

## Overall Verdict

The presentation is **very strong for an interim**. The narrative arc is clean, the literature chain is logically connected, and the challenges + mitigations slide pair shows forward thinking that most teams skip entirely. The design is professional and consistent. However, there is one critical gap that will draw questions from the professor: **zero code modules are implemented**. Everything after data decision is marked "Planned." You need to be ready to explain that gap confidently and point to a concrete 9-day plan.

---

## Slide-by-Slide Notes

### Slide 1 — Title
Good. All three names listed, "Interim Presentation" clearly labelled. Nothing missing.

### Slide 2 — Agenda
10 items is a lot for an interim, but you cover all of them. Good discipline.

### Slide 3 — Problem Statement
The problem box is precise and correct. The "Core Challenge" bullet (LiDAR hits edges → sparse data) directly motivates your two-tier approach later. This is your hook — lean on it when presenting.

**Minor gap:** You don't yet say what metric proves you solved the problem. Mention NMSE/IoBB here so the evaluator knows success has a measurable definition.

### Slide 4 — Literature Review
**Best slide in the deck.** The three-row table builds a clean progression: Elfes invented it → Stachniss formalized it → Önen extended it. The bold caption at the bottom is a good summary sentence to say out loud. No gaps.

### Slide 5 — Core Concepts Understood
The ray diagram is excellent. The three-assumption table is correct.

**Gap:** The slide says "PC-SBL deliberately breaks Assumption 3" but doesn't show *how*. One sentence like "it introduces a spatial prior C that couples neighboring cells" would pre-empt the inevitable professor question.

**Missing topic:** The inverse sensor model (Bresenham ray casting) is the core implementation step but has no dedicated slide. It's implied by the ray diagram but never named. A professor who knows the field will ask "how do you trace the ray?" Add this, even as a half-slide or a bullet in Approach Overview.

### Slide 6 — Approach Overview
The two-tier comparison table is well-structured and honest — Tier 1 is labeled "validate pipeline, sanity check," not overclaimed as a result. Good framing.

**Note:** Row order (Method, Sparsity, Spatial correlation, Output, Role) is logical. No changes needed.

### Slide 7 — System Architecture
The flowchart accurately matches the text description. Input → Preprocessing → Grid → Tier 1 / Tier 2 → Output is the right pipeline.

**Gap:** "NMSE / IoBB" appears in the output box but it's not yet explained here. A short annotation "(see Dataset slide)" or "(ground truth from nuScenes annotations)" would help the evaluator follow the logic without jumping to slide 8.

### Slide 8 — Dataset Specifications
Precise and correct. The 4 preprocessing steps are the exact steps you will implement.

**Gap to be aware of (not necessarily a slide issue):** IoBB requires extracting BEV bounding boxes from nuScenes `sample_annotation.json` and mapping them to your 80×80 grid. This step is non-trivial and is not discussed anywhere. Before the live presentation, know your answer to "how do you get ground truth occupancy?"

**Correction:** The grid is 80×80 cells (40m / 0.5m = 80), giving N = 6,400. This is correct but stating "80×80 grid" explicitly would avoid confusion.

### Slide 9 — Progress Status
**This is the slide that will get the hardest questions.**

What's strong: the "COMPLETED REGARDLESS OF CODE STATUS" framing is honest and shows intellectual ownership of the problem. Reading all papers and fully specifying the architecture are real deliverables.

What's weak: 6 out of 7 modules are "Planned" with no partially-done status. You are 4 days into Phase 3 (Classical Bayesian, started May 19) with nothing running yet. A professor seeing this will ask: "When will you have something running?" Have a confident, specific answer: **"We will have the Bayesian baseline producing a visualized grid by June 1st — the code is being written this weekend."** (And then actually write it — see the code in this repo.)

### Slide 10 — Key Challenges Identified
**Second-best slide.** Identifying four concrete, named challenges shows the team actually thought through the implementation. The C matrix size challenge (2000×6400) is particularly sophisticated.

**Minor correction:** After filtering a real nuScenes scan to ±20m range, you get ~5,000–15,000 points, not just M=1,000 reflections. The C matrix is therefore larger (up to 15,000×6,400 = 96M entries). Your sparse matrix mitigation still holds — the point count makes it even more important.

### Slide 11 — Mitigation Strategies
All four mitigations are technically sound:
- nuScenes devkit ego-frame transform: correct, this eliminates the alignment problem
- EM iteration cap + fallback to Tier 1: practical and defensible  
- `scipy.sparse.csr_matrix`: correct approach
- Skip focus masking, use range filter only: appropriate scope reduction for a course project

**Strong slide.** No major gaps.

### Slide 12 — Timeline
The roadmap visual is clear. Color coding (green = done, teal = active, red = planned) is intuitive.

**Feasibility assessment (honest):**

| Phase | Dates | Assessment |
|---|---|---|
| Phase 1: Literature | May 13–18 | Done — on time |
| Phase 2: Interim Prep | May 19–25 | On track (today = May 23) |
| Phase 3: Classical Bayesian | May 19–Jun 01 | **Tight — 9 days left, zero code. Doable if you start today.** |
| Phase 4: PC-SBL | Jun 02–15 | **Ambitious.** 2 weeks for a sparse EM algorithm. Feasible only if Tier 1 is clean and the team is all-in. |
| Phase 5: Evaluation | Jun 16–25 | Reasonable |
| Phase 6: Report + Final | Jun 26–Jul 03 | **Tight.** 8 days for a full report + final slides. Start the report skeleton in parallel with Phase 4. |

**Overall timeline verdict: Doable, with no slack.** The scope is appropriate for a semester course. PC-SBL is the high-risk item — if it stalls, Tier 1 alone with strong evaluation and an honest gap analysis is still a complete, presentable project.

### Slide 13 — Thank You
Fine.

---

## What to Add Before the Live Presentation (June 01)

1. **Show something running.** By June 01, you should be able to demo a terminal output or a grid image from one nuScenes scan. Even a text-printed 80×80 grid with values is better than nothing.
2. **One sentence on the inverse sensor model** (Bresenham ray casting) — the core step that connects LiDAR points to log-odds updates.
3. **One sentence on IoBB ground truth extraction** — show you know how nuScenes annotations translate to grid cells.
4. **Update the progress table** to reflect any modules completed by June 01.

---

## Is the Scope Doable?

**Yes — if the team starts coding today.**

The Classical Bayesian pipeline (Phases 3) is 5–6 focused coding days:
- Day 1–2: data loader + preprocessing → point cloud as numpy array
- Day 3: occupancy grid structure + Bresenham ray casting
- Day 4: Bayesian log-odds update loop over all points
- Day 5: visualizer + sanity check on one scene
- Day 6: buffer for debugging

The PC-SBL phase (Phase 4) is the research-difficulty item. Two weeks is sufficient for a working prototype if you implement only the core EM loop from the 2024 paper and use the sparse matrix from the start.

The evaluation metrics (Phase 5) are straightforward once the grid exists — NMSE is one `numpy` expression; IoBB requires parsing `sample_annotation.json` and is maybe a day of work.

**The main risk is not scope — it's starting too late.** Start writing code today.
