"""
runner.py — Evaluation runner for the clinical RAG Q&A system.

Loads ground-truth questions from evaluation/ground_truth/questions.json,
runs each through the Q&A pipeline, judges the answer with an LLM judge,
and writes results to config.data_dir/eval_results.json.

Exits immediately (with a log message) when config.evaluation.enabled is False.
"""

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_evaluation(
    config,
    db_path: str,
    pinecone_index,
    reid_map_path: str,
) -> None:
    """Run the full evaluation suite.

    Parameters
    ----------
    config:          AppConfig instance.
    db_path:         Path to the SQLite database.
    pinecone_index:  Initialised Pinecone index object (or None if offline).
    reid_map_path:   Path to the re-identification map JSON file.
    """
    if not config.evaluation.enabled:
        logger.info("Evaluation disabled in config — skipping.")
        return

    # Load ground-truth questions
    questions_path = os.path.join(
        os.path.dirname(__file__), "ground_truth", "questions.json"
    )

    if not os.path.isfile(questions_path):
        logger.error(
            "Evaluation: ground_truth/questions.json not found at %s", questions_path
        )
        return

    with open(questions_path, encoding="utf-8") as fh:
        ground_truth = json.load(fh)

    if not ground_truth:
        logger.warning("Evaluation: questions.json is empty — nothing to evaluate.")
        return

    from app.generation.qa import run_query
    from evaluation.judge import judge_answer

    results = []

    for item in ground_truth:
        question = item["question"]
        patient_id = item["patient_id"]
        expected_answer = item["expected_answer"]

        logger.info("Eval: running query — %s", question[:60])

        try:
            answer = asyncio.run(
                run_query(
                    question=question,
                    patient_id=patient_id,
                    config=config,
                    db_path=db_path,
                    pinecone_index=pinecone_index,
                    reid_map_path=reid_map_path,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Eval: run_query raised — %s", exc)
            answer = f"[error: {exc}]"

        scores = judge_answer(question, answer, expected_answer, config)
        results.append(
            {"question": question, "answer": answer, "scores": scores}
        )
        logger.info("Eval: Q=%s... scores=%s", question[:40], scores)

    # Persist results
    output_path = os.path.join(config.data_dir, "eval_results.json")
    os.makedirs(config.data_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

    # Console report
    print("\n=== Evaluation Results ===")
    for r in results:
        print(f"Q: {r['question'][:60]}")
        print(f"   Scores: {r['scores']}")
    print(f"\nFull results saved to {output_path}")
