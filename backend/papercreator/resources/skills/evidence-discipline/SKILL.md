---
name: Evidence discipline
id: evidence-discipline
description: Hard rules against unsupported claims and fabricated citations. Recommended for every project.
version: 1.0.0
origin: builtin
priority: 10
applies_to: [all]
triggers:
  - accuracy
  - no hallucination
  - evidence
  - rigour
tags: [quality, safety]
---

Accuracy takes precedence over fluency and over completeness.

**Citations**
- Cite only `[KEY]` markers that appear in the paper list you were given. A marker
  you cannot find in that list is a fabrication - do not write it.
- Never state an author name, year, venue, dataset, or numeric result that is not
  present in the provided material.
- Attribute each specific finding to the single paper it came from, not to a group
  of citations.

**Claims**
- Before writing any claim, identify which provided paper supports it. If none
  does, either remove the claim or mark it as an open question in the text.
- Distinguish what a paper *reports* from what it *proves*. Use "reports",
  "observes", "argues" unless the provided material describes a proof.
- Do not generalise from one study to a field. "One study found X [KEY]" is not
  "X is established".

**Quantities**
- Reproduce numbers exactly as given, with their units and their conditions
  ("94.2% top-1 on ImageNet" not "about 94%").
- If the provided material gives no number, write the qualitative statement and do
  not invent a magnitude.

**Gaps and novelty**
- Gap statements derived from automated landscape analysis are hypotheses. Write
  them as "we found no work addressing X in the surveyed literature", never as
  "no work exists".
- Do not claim novelty for the author's contribution beyond what the provided
  literature allows you to check.

**When you cannot comply**
- If the brief asks for content the provided literature cannot support, write what
  is supportable and state plainly what is missing. Do not fill the gap with
  plausible-sounding text.
