"""Offline checks for the live-log audit and the Word builder used by the IEEE paper."""
import importlib.util
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load("research/ieee_paper/experiments/scripts/live_audit.py", "live_audit")
builder = load("research/ieee_paper/build_docx.py", "build_docx")

LOG = [
    '10:00:00 [ANIMA] STARTUP mode="preview" provider_profile="gemini.yaml" rag="False" stt="True" tts="True" port="8768"',
    '10:00:01 [ANIMA] PLAYER_INPUT session="secret-session" request="r1" npc="ref:1" source="text" text="my private words"',
    '10:00:02 [ANIMA] PIPELINE_RESULT session="secret-session" request="r1" route="generate" status="model_success" turn_type="new" move="answer" model="m" calls="1"',
    '10:00:02 [ANIMA] TTS_READY request="r1" engine="Piper" voice="v" bytes="1"',
    '10:00:02 [ANIMA] OUTPUT_STAGED session="secret-session" request="r1" delivery="d" npc="ref:1" mode="speech" text="reply words" audio="True"',
    '10:00:03 [ANIMA] TURN_TIMING session="secret-session" request="r1" outcome="started" source="text" turn_type="new" move="answer" total_ms="100.0" model_ms="90.0" tts_ms="5.0" staging_ms="1.0" native_start_ms="2.0" pipeline_ms="91.0"',
    '10:00:03 [ANIMA] NATIVE_ACK session="secret-session" request="r1" delivery="d" status="started" audio="unreported" memory_committed="False"',
    '10:00:05 [ANIMA] NATIVE_ACK session="secret-session" request="r1" delivery="d" status="completed" audio="none" memory_committed="True"',
    '10:00:06 [ANIMA] PIPELINE_RESULT session="secret-session" request="r2" route="authored" status="authored_success" model="None" calls="0"',
    '10:00:06 [ANIMA] TURN_TIMING session="secret-session" request="r2" outcome="failed" source="text" total_ms="50.0" model_ms="none" tts_ms="5.0" staging_ms="1.0" native_start_ms="2.0" pipeline_ms="1.0"',
    '10:00:06 [ANIMA] NATIVE_ACK session="secret-session" request="r2" delivery="e" status="failed" audio="unreported" memory_committed="False"',
    '10:00:07 [ANIMA] SOCIAL_STATE request="r3" revision="2" memory_exchanges="3"',
]


def test_log_audit_joins_the_terminal_receipt_and_counts_model_use():
    report = audit.parse_log(LOG)
    assert report["timed_turns"] == 2
    assert report["receipts"] == {"completed": 1, "failed": 1}  # the started receipt is not terminal
    assert report["memory_committed"] == 1
    assert report["model_calls"] == 1
    assert report["routes"] == {"generate": 1, "authored": 1}
    assert report["moves"] == {"answer": 1}
    assert report["max_memory_exchanges"] == 3
    assert report["startups_with_rag"] == 0
    assert report["model_ms"]["n"] == 1  # "none" is not read as zero


def test_log_audit_never_copies_player_text_replies_or_session_ids():
    published = str(audit.parse_log(LOG))
    for private in ("my private words", "reply words", "secret-session"):
        assert private not in published


def test_black_fraction_separates_blank_half_and_lit_frames():
    half = Image.new("L", (100, 100), 255)
    half.paste(0, (0, 0, 50, 100))
    assert audit.black_fraction(Image.new("RGB", (640, 360))) == 1.0
    assert audit.black_fraction(Image.new("RGB", (640, 360), (200, 200, 200))) == 0.0
    assert audit.black_fraction(half) == pytest.approx(0.5, abs=0.02)


def test_frame_audit_counts_duplicates_as_one_distinct_frame(tmp_path):
    for name, level in (("a", 0), ("b", 0), ("c", 200)):
        Image.new("RGB", (64, 64), (level, level, level)).save(tmp_path / f"{name}.png")
    report = audit.frame_audit(sorted(tmp_path.glob("*.png")))
    assert report["frames"] == 3 and report["distinct_frames"] == 2
    assert report["frames_at_least_half_black"] == 2 and report["distinct_at_least_90pct_black"] == 1


def test_numbering_follows_source_order_with_roman_tables():
    tex = r"\begin{figure}\label{fig:a}\end{figure}\begin{table}\label{tab:a}\end{table}\begin{figure*}\label{fig:b}\end{figure*}\begin{table*}\label{tab:b}\end{table*}"
    assert builder.label_numbers(tex) == {"fig:a": "1", "tab:a": "I", "fig:b": "2", "tab:b": "II"}


def test_citations_references_and_punctuation_resolve():
    text = builder.plain(r"See~\cite{b,a} and Fig.~\ref{fig:x}, 25\% ``ok'' 12--45", {"a": 1, "b": 2}, {"fig:x": "3"})
    assert text == "See\u00a0[2], [1] and Fig.\u00a03, 25% “ok” 12–45"
    with pytest.raises(KeyError):
        builder.plain(r"\cite{missing}", {}, {})


def test_runs_style_text_and_math_and_refuse_unknown_latex():
    assert builder.runs(r"a \textbf{RQ1} b") == [("", "a "), ("bold", "RQ1"), ("", " b")]
    assert builder.runs(r"$G_\theta$") == [("italic", "G"), ("sub", "θ")]
    with pytest.raises(ValueError, match="unhandled LaTeX"):
        builder.runs(r"text \unknowncommand{x}")


def test_table_rows_keep_cells_and_ignore_rules():
    body = "\\begin{tabular}{ll}\n\\toprule\nA & B \\\\\n\\midrule\nC \\& D & E \\\\\n\\bottomrule\n\\end{tabular}"
    assert builder.table_rows(body) == [["A", "B"], ["C \\& D", "E"]]


def test_bibliography_is_numbered_and_free_of_latex():
    bbl = "\\begin{thebibliography}{1}\n\\bibitem{a}\nG.~Rade\\v z, ``A title,'' \\emph{Venue}, pp. 13\\,153--13\\,187, 2026.\n\\end{thebibliography}"
    assert builder.citation_numbers(bbl) == {"a": 1}
    assert builder.bib_items(bbl) == ["G. Radež, “A title,” Venue, pp. 13153–13187, 2026."]


def test_percentile_uses_an_observed_value_and_summary_reports_it():
    values = [float(v) for v in range(1, 16)]
    assert audit.rank(values, 0.9) == 14.0
    assert audit.summary(values)["p90"] == 14.0
    assert audit.summary([]) is None


def test_pre_instrumentation_generated_turns_are_counted_separately():
    early = ['09:00:00 [ANIMA] PIPELINE_RESULT request="r0" route="generate" status="model_success" model="m" calls="1"',
             '09:00:01 [ANIMA] NATIVE_ACK request="r0" delivery="x" status="interrupted"']
    report = audit.parse_log(early + LOG)
    assert report["generated_before_instrumentation"] == {"turns": 1, "receipts": {"interrupted": 1}}
    assert report["timed_turns"] == 2


def test_math_handles_subscripts_greek_and_times():
    assert builder.math_runs(r"m_0=\pi") == [("italic", "m"), ("sub", "0"), ("italic", "=π")]
    assert builder.math_runs(r"1711\times1048") == [("italic", "1711×1048")]
