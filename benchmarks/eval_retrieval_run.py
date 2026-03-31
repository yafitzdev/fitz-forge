# benchmarks/eval_retrieval_run.py
"""Run retrieval eval with PyCharm's Run button. Edit settings below."""

SOURCE_DIR = "C:/Users/yanfi/PycharmProjects/fitz-sage"
CATEGORY = None  # e.g. "retrieval", "ingestion", None for all
IDS = None       # e.g. "1,2,3", None for all
VERBOSE = True

if __name__ == "__main__":
    from benchmarks.eval_retrieval import run
    run(source_dir=SOURCE_DIR, category=CATEGORY, ids=IDS, verbose=VERBOSE)
