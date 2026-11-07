# Submission rewrite - 2026-11-06

Goal from the owner: a clean 6 to 8 page paper that leads with the generalized orchestration layer, the peasant and king example and avoiding custom model training, with strong diagrams, few tables and no filler. Result: 7 pages, 6 figures, 3 tables. No API, game or CV-enabled run was made for this revision. CV stays off.

Removed from the page, kept in the repository: the full 24-turn dialogue tables (`tables/multiturn_*.tex`, `experiments/raw_results/multiturn_dialogue.json`). Unused figure source kept: `figure_sources/knowledge_ingestion.tex`.

New headline evidence: the knowledge-scope study, rerun into `experiments/raw_results/knowledge_scope_rerun.json`. Other evidence files:

| File | Content |
|---|---|
| `current_system_study.json` | Director conformance, 128 paired descriptor turns (66 reached a prompt), name-check leak, CV service overhead with a mocked endpoint |
| `orchestration_rerun.json` | Behaviour, faults and provider swap on the working tree. `too_long` shows `passed: false` only because the CS3235 study still expects the older outcome |
| `skyrim_live_audit.json` | First 354 log lines, 23 timed turns, without player text, reply text or session IDs |
| `cv_capture_audit.json` | Hashes and pixel statistics for 100 frames from the original BitBlt capture, none of the frames themselves |
| `cv_smoke_2026-11-06.json` | Reduced smoke-test output from a separate session, before and after the capture and prompt fixes, plus reported capture figures; not an accuracy study |
| `reruns_2026-11-06.json` | Retrieval rerun, written by a separate session; its P@1, Recall@3 and MRR match a run by this session |

An independent reviewer session raised the following. Each was checked against code or data before use.

- Applied: vision reaches the name check as well as the prompt (reworded and measured; code unchanged, a decision for the owner). Over-long replies are now shortened. Pairs reaching a prompt are stated. The live result separates 9 model replies from fallbacks and authored lines. Latency uses the documented p90 targets. Fallback causes are labelled as inferred. Capture is on by default and has a third trigger. Schema wording is softened. Figures redrawn.
- Open, needs the owner: the code was not committed, so results describe the 6 Nov working tree on top of `2e04990`, which other sessions were still changing. To cite a clean hash, commit, then rerun the offline studies. The title's claims were left as approved, though the reviewer considers them strong. The CS3235 figures and its `too_long` table row were not changed.

Self-review of this rewrite, not independent. The Word file was checked by structure and a browser previewer that does not lay out continuous sections, not in Microsoft Word.

# Research report expansion - 2026-11-03

Scope: report-only work while another worker repairs the system. No runtime, adapter, game installation, provider configuration or teammate code was changed. Previous manuscript/PDF/source ZIP retained locally under tmp/pdfs/report-expansion-original/. No new provider calls, live game actions or screenshot captures were performed in this revision.

## Plan and plan self-review

Preserve the research contribution, authorship, title, existing raw observations and editable TikZ figures. Audit saved records, expose every completed paired response, separate input/route/output/delivery counts, narrow research questions to their evidence, expand the conclusion and future work, then compile/render all pages and independently rebuild the source ZIP. This was a report-only plan and labelled self-review using the repository review-npc-design criteria; no independent agent was delegated. Document checks apply; production unit/regression tests are not claimed for these manuscript edits.

## Changes

- Results now have Dialogue Evaluation and Test Coverage, Retrieval Effectiveness, RAG-Enabled Dialogue Comparison, In-Game Integration and Dialogue Delivery, Runtime Performance, Knowledge Update Demonstration and Discussion subsections.
- Table I separates evaluation units and routes. Table II shows every completed matched question with both full responses, expected targets and literal-match indicators. Table III preserves paired timing. Table IV documents native delivery. Table V preserves warm retrieval timings.
- Three conclusion paragraphs synthesize the implemented architecture and measured evidence. Six future-work directions address specific current limitations.
- RQ3/RQ4 now ask about implemented controls and substitution boundaries; they no longer imply a completed semantic or multi-provider benchmark. RQ5 is explicitly retrieval-level.
- Added Persona-Chat to related work/bibliography, checked against the primary ACL entry. Scholarly references retain full author/venue/page data and DOI metadata where supplied; long online URLs are reserved for documentation/model-card entries.
- Defined the retrieval-query builder, source binding, prompt packer and generator symbols. Fixed the unclosed uppercase greeting quotation. Improved table wrapping without reducing table type below small (9 pt in IEEEtran).

## Traceability and counts

D1-D7 map in order to forge-location, forge-service, clinic-location, burn-salve, inn-name, inn-soup and guard-duty in experiments/raw_results/paired_generation.json. Both rag_enabled conditions are reproduced verbatim. Four authored personas were exercised. Eight distinct model-study inputs produced 14 accepted generated responses, including one repaired response, and one authored error fallback. The unmatched eighth input guard-location and its actual fallback are quoted in the prose. There were 17 historical provider requests including repair attempts. No fallback or delivery acknowledgement enters the seven-pair target-match denominator.

experiments/raw_results/anima_uppercase_attribution.json supplies the Whiterun Guard HELLO input, What do you need? authored output, zero model calls and started/completed receipts. experiments/raw_results/native_compatibility_followup.json supplies runtime 1.7.104.0, connector 0.2.0, greeting route, two receipt pairs and DLL hash; the existing native follow-up narrative records Jenassa context. The interruption pair has no NPC field, so Table IV does not invent that identity. These are historical demonstrations, not new trials. Hello and HELLO are two case-sensitive strings; combined with eight model questions they make ten input strings, not ten successful generated cases.

Retrieval/update data come from experiments/raw_results/retrieval_update.json and cold_query.json. Independent recalculation of the raw ranked_sources/relevant_sources, paired text targets and timing samples is in experiments/analysis/report_expansion_numeric_audit.json. No semantic correctness percentage or human ratings were fabricated.

## Sources checked

- Persona-Chat: https://aclanthology.org/P18-1205/
- Character-LLM: https://aclanthology.org/2023.emnlp-main.814/
- RAGAs: https://aclanthology.org/2024.eacl-demo.16/
- Game dialogue: https://aclanthology.org/2022.emnlp-main.637/

## Implementation self-review and remaining gates

Resolved: two-example selection hid the rest of the seven pairs; every output is now visible. Resolved: planned boundary prompts could be mistaken for completed coverage; counts now distinguish them. Resolved: broad RQs implied unmeasured semantic/provider conclusions; their scope now matches the evidence. Resolved: native interruption receipts lacked NPC identity; it is explicitly unspecified.

Pending: new 12-20 distinct successful cases, independent semantic/persona ratings, multiple real providers, a post-update generated answer, and genuine visible generated gameplay. Existing connector image frame-dab358e97ae141c693ebb51a7d02c1e4.png was visually inspected and is black; originals were preserved and no unusable/synthetic image was inserted. System/capture fixes are owned by the other worker. This revised report can be reviewed now, but the complete experimental/screenshot request remains open.

## Validation

Final page count: 7. Tectonic compile, source/label/citation/assets checks, raw-response/metric verification, page-by-page visual review and independent extracted-ZIP rebuild are recorded under research/evidence/report_expansion_*. No runtime regression or new live test is claimed. Standard IEEEtran font, margins and line spacing are preserved. Remaining underfull spacing notices require visual inspection; overfull boxes, missing references and font substitution are rejected by the validator.
