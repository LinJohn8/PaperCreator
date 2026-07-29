---
name: IEEE conference style
id: ieee-conference-style
description: Conventions for IEEE-format conference papers (8-10 pages, numeric citations).
version: 1.0.0
origin: builtin
priority: 20
applies_to: [writer, reviser, critic, outliner]
triggers:
  - IEEE
  - conference paper
  - ICASSP
  - ICRA
  - CVPR
tags: [venue, style, english]
---

Write for an IEEE-format conference paper.

**Structure and length**
- Assume a hard 8-page limit including references. Introduction and related work
  together should not exceed 1.5 pages.
- Use the canonical section order: Introduction, Related Work, Method,
  Experiments, Results, Discussion, Conclusion. Merge Results into Experiments if
  the evaluation is small.
- State contributions as an explicit bulleted list at the end of the
  Introduction, three to four items, each one sentence.

**Prose conventions**
- Present tense for the proposed method ("our model encodes..."), past tense for
  experiments performed ("we trained...").
- Use "we" for the authors' actions. Avoid "the authors" when referring to
  yourselves.
- First use of any acronym: expanded form followed by the acronym in parentheses.
  Every use after that is the acronym alone.
- Do not use contractions. Do not use "very", "really", "quite", "a lot".

**Claims and evidence**
- Every quantitative claim must name the table or figure that supports it, or the
  citation it comes from.
- Do not claim state of the art unless a cited comparison in the provided
  literature supports it. If the comparison is not available, write "competitive
  with" and name what was compared.
- Describe limitations in the Discussion, not the Conclusion.

**References**
- Refer to prior work by what it does, not by its authors' names in running text:
  prefer "a graph attention approach [KEY]" over "Smith et al. [KEY] proposed".
- Group related citations when several works share an approach.
