# Accuracy Optimization Design Review

## Reviewed risks

- N-gram overlap was being promoted to HIS table evidence.
- Feedback vector distances were queried but ignored; keyword substring hits
  could import unrelated examples.
- Feedback validation checked syntax and safety but not question/SQL meaning.
- Evidence objects with arbitrary `table_name` values could unlock generation.
- `OUTSIDE_RETRIEVED_EVIDENCE` was only a warning, and intent signals were not
  checked against the candidate AST.

## Decisions

1. Keep lexical/vector retrieval as ranking aids, but require explicit
   provenance and thresholds for strong evidence.
2. Make semantic checks deterministic and conservative. Reject only signals
   that can be proven missing; retain a warning for ambiguous, non-deterministic
   language rather than inventing a SQL interpretation.
3. Use one AST policy implementation for model candidates and feedback
   approval. This avoids a valid-feedback bypass of generation safeguards.
4. Preserve compatibility for direct low-level policy callers by making strict
   evidence and intent checks opt-in; production generation/feedback always
   opts in.
5. Treat vector index metadata as part of correctness, not merely performance;
   an embedding-model or preprocessing change invalidates old feedback vectors.

## Rejected alternatives

- Increasing top-k or prompt size: adds noise and cost without improving
  grounding.
- Asking the model to self-report whether it used a table: not a security or
  correctness boundary.
- Executing generated SQL to test meaning: violates the project boundary and
  would require target-database credentials.

## Residual risks

Natural-language time ranges and business metrics can remain ambiguous. The
system should return a clarification/local `NO_SQL` result when deterministic
signals cannot be mapped, and future evaluation data should measure false
rejection separately from semantic accuracy.

## Tranche Review

The proposed retrieval changes were reviewed against three failure modes:

1. A stale vector collection must fail closed and fall back to lexical
   retrieval, never silently mix old table content with current SQLite rows.
2. A threshold must be applied before feedback fusion; sorting raw Chroma
   results first would still let an irrelevant vector fill the context.
3. Bidirectional relationship traversal must remain bounded and provenance
   marked as expansion, otherwise parent-to-child joins could become an
   accidental authorization signal.

The design therefore keeps thresholds configurable, makes exact identifiers
and validated HIS bindings stronger than learned similarity, and records
stale-binding warnings rather than dropping an entire semantic term.
