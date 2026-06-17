HYPOTHESIS_SYSTEM_PROMPT = """Generate ranked investigation hypotheses using only supplied evidence.
Every hypothesis must cite existing evidence IDs, remain probabilistic, identify missing evidence,
and propose concrete next checks. Never claim a final root cause."""

VERIFIER_SYSTEM_PROMPT = """Verify that every cited evidence ID exists. Flag unsupported claims,
reduce confidence when support is weak, and state which missing evidence prevents stronger conclusions."""
