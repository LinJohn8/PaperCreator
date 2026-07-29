"""Paper structure templates.

A template is the section skeleton for a kind of paper. It answers "what sections
should exist and roughly how long" before any AI is involved, which matters for
two reasons: the user can start writing immediately in a sensible structure, and
the planner agent has a concrete starting point rather than inventing an
organisation from nothing.

Templates are data, not code, so adding one is a dict entry. Each section carries
``guidance`` that becomes the writer agent's brief when that template is applied.
"""

from __future__ import annotations

from typing import Any

# Word targets assume a full paper of the stated total; the API scales them
# proportionally when the user asks for a different length.
TEMPLATES: dict[str, dict[str, Any]] = {
    "generic": {
        "name": "Generic research paper",
        "name_zh": "通用研究论文",
        "description": "Standard structure suitable for most venues.",
        "total_words": 6000,
        "sections": [
            {"key": "abstract", "title": "Abstract", "title_zh": "摘要",
             "target_words": 200, "level": 1,
             "guidance": "State the problem, what was done, the key result with a "
                         "number if available, and why it matters. No citations. "
                         "One paragraph."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言",
             "target_words": 900, "level": 1,
             "guidance": "Establish the problem and why it matters, summarise what "
                         "existing work achieves and where it falls short, state "
                         "the contribution, and close with an explicit list of "
                         "contributions."},
            {"key": "related-work", "title": "Related Work", "title_zh": "相关工作",
             "target_words": 1000, "level": 1,
             "guidance": "Organise by approach family, not by paper. For each "
                         "family: what it assumes, what it achieves, where it "
                         "breaks. End by positioning this work against them."},
            {"key": "method", "title": "Method", "title_zh": "方法",
             "target_words": 1500, "level": 1,
             "guidance": "Define the problem formally, then describe the approach "
                         "in the order a reader would implement it. State every "
                         "assumption. Explain design choices, not just the design."},
            {"key": "experiments", "title": "Experiments", "title_zh": "实验",
             "target_words": 1100, "level": 1,
             "guidance": "Describe datasets, baselines, metrics and protocol "
                         "precisely enough to reproduce. Name what is held "
                         "constant across comparisons."},
            {"key": "results", "title": "Results", "title_zh": "实验结果",
             "target_words": 900, "level": 1,
             "guidance": "Report findings against each research question. "
                         "Reference the table or figure for every number. Report "
                         "negative and inconclusive results too."},
            {"key": "discussion", "title": "Discussion", "title_zh": "讨论",
             "target_words": 500, "level": 1,
             "guidance": "Interpret what the results mean, state the limitations "
                         "honestly, and identify threats to validity."},
            {"key": "conclusion", "title": "Conclusion", "title_zh": "结论",
             "target_words": 300, "level": 1,
             "guidance": "Restate the contribution and the strongest evidence for "
                         "it, then name concrete future work. No new claims."},
        ],
    },
    "survey": {
        "name": "Survey / review",
        "name_zh": "综述论文",
        "description": "Literature survey organised around an explicit taxonomy.",
        "total_words": 9000,
        "sections": [
            {"key": "abstract", "title": "Abstract", "title_zh": "摘要",
             "target_words": 250, "level": 1,
             "guidance": "State the field surveyed, the organising axis, the "
                         "number of works covered, and the main open problems "
                         "identified."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言",
             "target_words": 1000, "level": 1,
             "guidance": "Motivate why the field needs a survey now. State the "
                         "scope explicitly: what is included, what is excluded, "
                         "and why. Declare the organising axis."},
            {"key": "methodology", "title": "Review Methodology",
             "title_zh": "综述方法", "target_words": 600, "level": 1,
             "guidance": "Describe the search: which databases, which queries, "
                         "which date range, how many results, how they were "
                         "filtered, and the inclusion criteria. Give numbers."},
            {"key": "background", "title": "Background and Taxonomy",
             "title_zh": "背景与分类体系", "target_words": 1200, "level": 1,
             "guidance": "Define the concepts and notation used throughout, then "
                         "present the taxonomy that structures the rest of the "
                         "survey."},
            {"key": "approaches", "title": "Approaches", "title_zh": "方法综述",
             "target_words": 2800, "level": 1,
             "guidance": "One subsection per taxonomy branch. Each describes a "
                         "class of approaches with several cited instances, its "
                         "assumptions, achievements and failure modes."},
            {"key": "comparison", "title": "Comparative Analysis",
             "title_zh": "对比分析", "target_words": 1200, "level": 1,
             "guidance": "Compare the classes on shared dimensions. Where "
                         "published numbers are not comparable, say so and "
                         "explain why."},
            {"key": "applications", "title": "Applications and Datasets",
             "title_zh": "应用与数据集", "target_words": 800, "level": 1,
             "guidance": "Summarise where these methods are applied and on which "
                         "datasets and benchmarks."},
            {"key": "open-problems", "title": "Open Problems",
             "title_zh": "开放问题", "target_words": 900, "level": 1,
             "guidance": "Each open problem must be tied to what the reviewed "
                         "literature does not address. Distinguish 'no work "
                         "found' from 'no work exists'."},
            {"key": "conclusion", "title": "Conclusion", "title_zh": "结论",
             "target_words": 400, "level": 1,
             "guidance": "Summarise the state of the field and the most promising "
                         "directions."},
        ],
    },
    "empirical": {
        "name": "Empirical study",
        "name_zh": "实证研究",
        "description": "Hypothesis-driven study with explicit research questions.",
        "total_words": 7000,
        "sections": [
            {"key": "abstract", "title": "Abstract", "title_zh": "摘要",
             "target_words": 220, "level": 1,
             "guidance": "State the research question, the study design, the "
                         "population or dataset, the main finding with effect "
                         "size, and the implication."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言",
             "target_words": 900, "level": 1,
             "guidance": "Motivate the question and end with explicitly numbered "
                         "research questions or hypotheses."},
            {"key": "related-work", "title": "Background", "title_zh": "研究背景",
             "target_words": 900, "level": 1,
             "guidance": "Cover prior findings on this question and the "
                         "disagreements this study addresses."},
            {"key": "study-design", "title": "Study Design", "title_zh": "研究设计",
             "target_words": 1200, "level": 1,
             "guidance": "Describe the design, subjects or data, variables, "
                         "instruments and procedure. Pre-register-level detail."},
            {"key": "analysis", "title": "Analysis", "title_zh": "分析方法",
             "target_words": 800, "level": 1,
             "guidance": "State the statistical or analytical methods, the "
                         "assumptions they require, and how those were checked."},
            {"key": "results", "title": "Results", "title_zh": "结果",
             "target_words": 1300, "level": 1,
             "guidance": "One subsection per research question. Report effect "
                         "sizes and uncertainty, not only significance."},
            {"key": "threats", "title": "Threats to Validity",
             "title_zh": "有效性威胁", "target_words": 600, "level": 1,
             "guidance": "Address internal, external, construct and conclusion "
                         "validity separately."},
            {"key": "discussion", "title": "Discussion", "title_zh": "讨论",
             "target_words": 700, "level": 1,
             "guidance": "Interpret findings against the prior work cited earlier. "
                         "State what would change the conclusion."},
            {"key": "conclusion", "title": "Conclusion", "title_zh": "结论",
             "target_words": 300, "level": 1,
             "guidance": "Answer each research question in one sentence."},
        ],
    },
    "short": {
        "name": "Short paper / workshop",
        "name_zh": "短文 / 工作论文",
        "description": "4-page structure with one focused contribution.",
        "total_words": 2800,
        "sections": [
            {"key": "abstract", "title": "Abstract", "title_zh": "摘要",
             "target_words": 150, "level": 1,
             "guidance": "One problem, one idea, one result."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言",
             "target_words": 600, "level": 1,
             "guidance": "Compress motivation, gap and contribution into three "
                         "paragraphs. Cite sparingly and only what is essential."},
            {"key": "approach", "title": "Approach", "title_zh": "方法",
             "target_words": 900, "level": 1,
             "guidance": "Describe only the novel part in detail; reference "
                         "standard components rather than explaining them."},
            {"key": "evaluation", "title": "Preliminary Evaluation",
             "title_zh": "初步评估", "target_words": 800, "level": 1,
             "guidance": "One convincing experiment. Be explicit that it is "
                         "preliminary and state what a full evaluation needs."},
            {"key": "conclusion", "title": "Conclusion and Next Steps",
             "title_zh": "结论与后续工作", "target_words": 350, "level": 1,
             "guidance": "State the finding and the concrete next experiment."},
        ],
    },
    "thesis-chapter": {
        "name": "Thesis chapter",
        "name_zh": "学位论文章节",
        "description": "Self-contained chapter with its own introduction and summary.",
        "total_words": 12000,
        "sections": [
            {"key": "chapter-intro", "title": "Chapter Introduction",
             "title_zh": "本章引言", "target_words": 800, "level": 1,
             "guidance": "Place this chapter in the thesis narrative: what the "
                         "previous chapter established, what this one adds."},
            {"key": "background", "title": "Background", "title_zh": "背景知识",
             "target_words": 2000, "level": 1,
             "guidance": "Full pedagogical treatment - a thesis examiner may not "
                         "share the paper reader's assumed background."},
            {"key": "related-work", "title": "Related Work", "title_zh": "相关工作",
             "target_words": 2200, "level": 1,
             "guidance": "Comprehensive rather than selective. Organise by theme "
                         "and state the relationship to this chapter's work."},
            {"key": "method", "title": "Proposed Approach", "title_zh": "研究方法",
             "target_words": 3000, "level": 1,
             "guidance": "Complete derivations and design rationale. Include the "
                         "alternatives considered and why they were rejected."},
            {"key": "experiments", "title": "Experimental Setup",
             "title_zh": "实验设置", "target_words": 1500, "level": 1,
             "guidance": "Full reproducibility detail including hyperparameters "
                         "and hardware."},
            {"key": "results", "title": "Results and Analysis",
             "title_zh": "结果与分析", "target_words": 1800, "level": 1,
             "guidance": "Results plus ablations, error analysis, and failure "
                         "cases."},
            {"key": "summary", "title": "Chapter Summary", "title_zh": "本章小结",
             "target_words": 700, "level": 1,
             "guidance": "Summarise the contribution and set up the next chapter."},
        ],
    },
}

# Original structure guides modelled on common publication families.  These are
# intentionally not copies of publisher/Overleaf class files: venue assets have
# independent licences and change over time.  The export layer may later attach
# an official class package without changing the manuscript's semantic outline.
TEMPLATES.update({
    "sci-imrad": {
        "name": "SCI-style IMRaD journal article",
        "name_zh": "SCI 风格 IMRaD 期刊论文",
        "category": "academic-journal",
        "description": "Full empirical science/engineering article with reproducibility and data statements.",
        "description_zh": "面向理工、生命科学实证研究的完整 IMRaD 结构，包含复现与数据声明。",
        "total_words": 7500,
        "sections": [
            {"key": "abstract", "title": "Abstract", "title_zh": "摘要", "target_words": 250, "level": 1, "guidance": "Use a structured problem-method-results-conclusion abstract with quantitative findings."},
            {"key": "keywords", "title": "Keywords", "title_zh": "关键词", "target_words": 40, "level": 1, "guidance": "Provide discoverable terms that are not merely words from the title."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言", "target_words": 1000, "level": 1, "guidance": "Establish evidence-backed context, the unresolved problem, objective and testable contribution."},
            {"key": "materials-methods", "title": "Materials and Methods", "title_zh": "材料与方法", "target_words": 1700, "level": 1, "guidance": "Describe data/materials, design, controls, ethics, statistics and parameters to reproduction depth."},
            {"key": "results", "title": "Results", "title_zh": "结果", "target_words": 1500, "level": 1, "guidance": "Report results in research-question order with effect sizes, uncertainty and figure/table references."},
            {"key": "discussion", "title": "Discussion", "title_zh": "讨论", "target_words": 1600, "level": 1, "guidance": "Interpret rather than repeat results; compare prior evidence, alternatives and boundary conditions."},
            {"key": "limitations", "title": "Limitations", "title_zh": "局限性", "target_words": 450, "level": 1, "guidance": "State sampling, measurement, validity and generalisation limits without formulaic disclaimers."},
            {"key": "conclusion", "title": "Conclusions", "title_zh": "结论", "target_words": 350, "level": 1, "guidance": "Answer the objective using only evidence established in the results."},
            {"key": "data-availability", "title": "Data and Code Availability", "title_zh": "数据与代码可用性", "target_words": 160, "level": 1, "guidance": "Give repository/accession identifiers, restrictions and reproducibility artefacts."},
            {"key": "declarations", "title": "Declarations", "title_zh": "声明", "target_words": 450, "level": 1, "guidance": "Record ethics, consent, funding, conflicts, author contributions and acknowledgements as applicable."},
        ],
    },
    "ssci-empirical": {
        "name": "SSCI-style social science article",
        "name_zh": "SSCI 风格社会科学实证论文",
        "category": "academic-journal",
        "description": "Theory-grounded quantitative, qualitative or mixed-method social research.",
        "description_zh": "适用于定量、定性或混合方法社会科学研究，强调理论、测量与研究伦理。",
        "total_words": 8500,
        "sections": [
            {"key": "abstract", "title": "Abstract", "title_zh": "摘要", "target_words": 250, "level": 1, "guidance": "State context, question, method/sample, principal findings and theoretical/practical implication."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言", "target_words": 900, "level": 1, "guidance": "Define the social problem and contribution to a named scholarly conversation."},
            {"key": "literature-theory", "title": "Literature Review and Theory", "title_zh": "文献综述与理论框架", "target_words": 1900, "level": 1, "guidance": "Synthesize constructs and competing explanations; derive a coherent conceptual model."},
            {"key": "questions-hypotheses", "title": "Research Questions / Hypotheses", "title_zh": "研究问题与假设", "target_words": 500, "level": 1, "guidance": "Make every question or hypothesis traceable to the preceding theory and measurable evidence."},
            {"key": "methodology", "title": "Methodology", "title_zh": "研究方法", "target_words": 1700, "level": 1, "guidance": "Explain sampling, setting, measures/coding, positionality, ethics, validity and analysis."},
            {"key": "findings", "title": "Findings", "title_zh": "研究发现", "target_words": 1500, "level": 1, "guidance": "Report evidence by question; preserve uncertainty, negative cases and representative qualitative evidence."},
            {"key": "discussion", "title": "Discussion", "title_zh": "讨论", "target_words": 1100, "level": 1, "guidance": "Return to theory, explain agreements and contradictions, and avoid causal claims unsupported by design."},
            {"key": "implications", "title": "Theoretical and Practical Implications", "title_zh": "理论与实践启示", "target_words": 350, "level": 1, "guidance": "Separate what changes scholarly understanding from context-bounded practice implications."},
            {"key": "limitations", "title": "Limitations and Future Research", "title_zh": "局限与未来研究", "target_words": 300, "level": 1, "guidance": "Tie future work to concrete validity or scope limits."},
            {"key": "conclusion", "title": "Conclusion", "title_zh": "结论", "target_words": 300, "level": 1, "guidance": "Give a concise answer and contribution; introduce no new evidence."},
        ],
    },
    "conference-full": {
        "name": "Full conference paper",
        "name_zh": "完整会议论文",
        "category": "conference",
        "description": "Compact 6–10 page technical conference narrative.",
        "description_zh": "适用于 6–10 页技术会议的紧凑问题—方法—证据叙事。",
        "total_words": 5200,
        "sections": [
            {"key": "abstract", "title": "Abstract", "title_zh": "摘要", "target_words": 180, "level": 1, "guidance": "Problem, technical idea, strongest result and significance in one paragraph."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言", "target_words": 750, "level": 1, "guidance": "Reach the contribution quickly and list concrete contributions."},
            {"key": "related-work", "title": "Related Work", "title_zh": "相关工作", "target_words": 650, "level": 1, "guidance": "Compare approach families and make the differentiator explicit."},
            {"key": "method", "title": "Method", "title_zh": "方法", "target_words": 1450, "level": 1, "guidance": "Specify the novel mechanism, assumptions, complexity and implementable detail."},
            {"key": "evaluation", "title": "Experimental Setup", "title_zh": "实验设置", "target_words": 700, "level": 1, "guidance": "Datasets, splits, baselines, metrics, budgets and reproducibility settings."},
            {"key": "results", "title": "Results and Analysis", "title_zh": "结果与分析", "target_words": 1000, "level": 1, "guidance": "Main comparison plus ablation, error analysis, uncertainty and failure cases."},
            {"key": "limitations", "title": "Limitations", "title_zh": "局限性", "target_words": 220, "level": 1, "guidance": "State known limits relevant to reviewers and downstream use."},
            {"key": "conclusion", "title": "Conclusion", "title_zh": "结论", "target_words": 250, "level": 1, "guidance": "Contribution and evidence, with no new claims."},
        ],
    },
    "systematic-review": {
        "name": "Systematic review / meta-analysis",
        "name_zh": "系统综述 / 元分析",
        "category": "academic-journal",
        "description": "Protocol-led evidence synthesis with auditable screening and bias assessment.",
        "description_zh": "面向可审计检索、筛选、偏倚评估和证据综合的系统综述。",
        "total_words": 9000,
        "sections": [
            {"key": "abstract", "title": "Structured Abstract", "title_zh": "结构式摘要", "target_words": 300, "level": 1, "guidance": "Background, objective, sources, eligibility, synthesis, results, limitations and conclusion."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言", "target_words": 800, "level": 1, "guidance": "Define the decision problem and why a systematic synthesis is needed."},
            {"key": "protocol", "title": "Protocol and Registration", "title_zh": "方案与注册", "target_words": 300, "level": 1, "guidance": "Give protocol, registration, deviations and dates."},
            {"key": "search-selection", "title": "Search and Study Selection", "title_zh": "检索与研究筛选", "target_words": 1300, "level": 1, "guidance": "Databases, exact reproducible queries, dates, deduplication, eligibility and reviewer process."},
            {"key": "extraction-bias", "title": "Data Extraction and Risk of Bias", "title_zh": "数据提取与偏倚风险", "target_words": 1000, "level": 1, "guidance": "Define fields, independent review, disagreements, missing data and appraisal instruments."},
            {"key": "synthesis", "title": "Synthesis Methods", "title_zh": "证据综合方法", "target_words": 900, "level": 1, "guidance": "Explain qualitative or statistical models, heterogeneity, sensitivity and publication-bias analysis."},
            {"key": "results", "title": "Results", "title_zh": "结果", "target_words": 2200, "level": 1, "guidance": "Flow counts, study characteristics, bias, individual findings, synthesis and uncertainty."},
            {"key": "discussion", "title": "Discussion", "title_zh": "讨论", "target_words": 1500, "level": 1, "guidance": "Certainty, applicability, heterogeneity and differences from prior reviews."},
            {"key": "conclusion", "title": "Conclusion", "title_zh": "结论", "target_words": 700, "level": 1, "guidance": "Proportion conclusions to evidence certainty and identify decision-relevant gaps."},
        ],
    },
    "research-poster": {
        "name": "Research poster content",
        "name_zh": "学术海报内容",
        "category": "poster-presentation",
        "description": "Concise content blocks for an Overleaf-style academic poster.",
        "description_zh": "用于 Overleaf 风格学术海报的精简内容块；排版仍需选择具体海报 class。",
        "total_words": 1000,
        "sections": [
            {"key": "takeaway", "title": "One-sentence Takeaway", "title_zh": "一句话结论", "target_words": 45, "level": 1, "guidance": "The one claim a passer-by should remember."},
            {"key": "motivation", "title": "Motivation and Objective", "title_zh": "背景与目标", "target_words": 170, "level": 1, "guidance": "Use short statements and one visual problem definition."},
            {"key": "method", "title": "Method", "title_zh": "方法", "target_words": 250, "level": 1, "guidance": "Explain the pipeline primarily through a figure and minimal labels."},
            {"key": "results", "title": "Key Results", "title_zh": "关键结果", "target_words": 300, "level": 1, "guidance": "Prioritise two or three legible quantitative findings and uncertainty."},
            {"key": "conclusion", "title": "Conclusion and Resources", "title_zh": "结论与资源", "target_words": 150, "level": 1, "guidance": "Takeaway, limitations and QR/repository/contact pointers."},
            {"key": "references", "title": "Selected References", "title_zh": "主要参考文献", "target_words": 85, "level": 1, "guidance": "Only references essential to interpreting the poster."},
        ],
    },
    "book-chapter": {
        "name": "Scholarly book chapter",
        "name_zh": "学术书籍章节",
        "category": "book",
        "description": "Long-form conceptual or methodological chapter for an edited volume.",
        "description_zh": "适用于编辑出版物的长篇概念、理论或方法章节。",
        "total_words": 10000,
        "sections": [
            {"key": "abstract", "title": "Abstract", "title_zh": "摘要", "target_words": 200, "level": 1, "guidance": "State chapter scope, argument and reader value."},
            {"key": "introduction", "title": "Introduction", "title_zh": "引言", "target_words": 900, "level": 1, "guidance": "Orient readers to the volume theme and chapter argument."},
            {"key": "foundations", "title": "Conceptual Foundations", "title_zh": "概念基础", "target_words": 1800, "level": 1, "guidance": "Define terms, intellectual history and disagreements."},
            {"key": "framework", "title": "Framework / Main Argument", "title_zh": "分析框架 / 核心论点", "target_words": 3000, "level": 1, "guidance": "Develop the central argument in a cumulative subsection structure."},
            {"key": "cases", "title": "Examples or Cases", "title_zh": "案例与示例", "target_words": 2200, "level": 1, "guidance": "Use cases to test and clarify the framework, including counterexamples."},
            {"key": "implications", "title": "Implications", "title_zh": "启示", "target_words": 1200, "level": 1, "guidance": "Separate theoretical, methodological and practical consequences."},
            {"key": "conclusion", "title": "Conclusion", "title_zh": "结论", "target_words": 700, "level": 1, "guidance": "Synthesize the argument and identify unresolved questions."},
        ],
    },
})

_LEGACY_DESCRIPTION_ZH = {
    "generic": "适用于大多数投稿场景的标准研究论文结构。",
    "survey": "围绕明确分类体系组织的文献综述结构。",
    "empirical": "包含显式研究问题或假设的实证研究结构。",
    "short": "围绕单一贡献组织的短论文或工作坊论文结构。",
    "thesis-chapter": "包含独立引言与小结的完整学位论文章节。",
}


def list_templates() -> list[dict[str, Any]]:
    """Template catalogue for the project creation dialog."""
    return [
        {
            "id": key,
            "name": value["name"],
            "name_zh": value["name_zh"],
            "description": value["description"],
            "description_zh": value.get(
                "description_zh", _LEGACY_DESCRIPTION_ZH.get(key, value["description"])
            ),
            "category": value.get("category", "general"),
            "source_kind": "built-in-structure",
            "license_note": (
                "Original PaperCreator structure guidance; not an official venue "
                "LaTeX class or a copied Overleaf template."
            ),
            "total_words": value["total_words"],
            "section_count": len(value["sections"]),
            "sections": [
                {"key": s["key"], "title": s["title"], "title_zh": s["title_zh"],
                 "target_words": s["target_words"]}
                for s in value["sections"]
            ],
        }
        for key, value in TEMPLATES.items()
    ]


def get_template(template_id: str) -> dict[str, Any]:
    """One template, falling back to ``generic`` for an unknown id.

    Falling back rather than raising: an unknown template id (from an old project
    row or a hand-edited project.json) should not make the project unopenable.
    """
    return TEMPLATES.get(template_id) or TEMPLATES["generic"]


def scaled_sections(
    template_id: str, target_words: int = 0
) -> list[dict[str, Any]]:
    """Template sections with word targets scaled to a requested total."""
    template = get_template(template_id)
    sections = [dict(s) for s in template["sections"]]
    if target_words and template["total_words"]:
        factor = target_words / template["total_words"]
        for section in sections:
            section["target_words"] = max(80, int(section["target_words"] * factor))
    for index, section in enumerate(sections):
        section["ordering"] = (index + 1) * 10
    return sections
