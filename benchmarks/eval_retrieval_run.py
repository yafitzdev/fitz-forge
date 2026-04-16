# benchmarks/eval_retrieval_run.py
"""Run retrieval eval with PyCharm's Run button. Edit settings below."""

SOURCE_DIR = "C:/Users/yanfi/PycharmProjects/fitz-sage"
CHALLENGES = None  # e.g. "streaming_implementation,ranking_explanation", None for all
VERBOSE = True

if __name__ == "__main__":
    from benchmarks.eval_retrieval import run
    run(source_dir=SOURCE_DIR, challenge=CHALLENGES, verbose=VERBOSE)
