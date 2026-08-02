# SQLGenie Accuracy Optimization Design

## Objective

Reduce semantically wrong SQL that is syntactically valid, without adding a
target-database connection or trusting model prose as a validator.

## Contracts

1. Schema evidence is typed and provenance-aware. An item is strong only when
   its table exists in the current schema and the retrieval layer explicitly
   marks a trusted source (explicit identifier, validated HIS binding, or a
   thresholded keyword/vector hit). Foreign-key expansion remains context
   only. Unknown or caller-forged evidence cannot unlock a model call.
2. HIS exact term/synonym matches may create bindings. Chinese n-gram overlap
   is ranking-only and must never create a binding by itself. Latin identifiers
   use identifier boundaries.
3. Feedback examples enter RAG only after dialect, read-only/schema, intent,
   and intent-to-AST checks pass. Vector examples require model/index metadata,
   dialect filtering, an absolute similarity threshold, and a leading-result
   margin. Keyword examples require selective, independent matches.
4. SQL policy validates the candidate AST against the analyzed intent. Explicit
   tables/columns and requested aggregate, grouping, sorting, distinct, ranking,
   latest/first, and time signals must be represented where the signal is
   deterministic. A strong-evidence set supplied by generation must intersect
   the physical AST tables; generation uses strict evidence mode.
5. A failed semantic or policy check follows the existing single repair path.
   A second failure returns `NO_SQL`; no third model call is added.

## Implementation units

- `his_semantics.py` and `rag.py`: exact-match provenance, selective lexical
  scoring, feedback vector metadata/thresholds, and feedback admission.
- `sql_policy.py`: optional intent-aware AST checks and strict evidence mode.
- `generation.py`: schema-bound strong-evidence filtering and passing intent to
  both validation calls.
- Regression tests: adversarial false HIS matches, low-similarity feedback,
  stale/unknown evidence, semantically incomplete SQL, and semantically wrong
  feedback.

## Compatibility and observability

Existing public callers that do not request strict evidence retain warning-only
diagnostics. The generation and feedback paths opt into strict mode. New
diagnostics use stable error codes and are included in the existing trace
payload; no secret or target-database credential is added.

## Acceptance criteria

- All existing tests remain green.
- New adversarial tests prove each contract above.
- A low-confidence request never reaches the remote model and yields a useful
  local reason.
- Approved feedback that answers a different table/metric/time question is
  rejected before indexing.

## Retrieval Accuracy Tranche (2026-08-01)

The next implementation tranche targets stale or over- permissive retrieval
without changing the generation API:

- Embedding inputs are typed (`document` versus `query`). BGE query
  instructions are never prepended to indexed documents.
- Schema and feedback collections carry the embedding model, preprocessing
  fingerprint, and source-content fingerprint. A mismatch invalidates the
  collection and triggers a rebuild; a Chroma hit is accepted only when its
  table id and content hash still match SQLite.
- Feedback vector hits require an absolute cosine-similarity threshold and are
  fused with selective keyword hits before top-k selection. Low-similarity
  vectors cannot displace an exact natural-language match.
- Foreign-key expansion uses a bounded bidirectional adjacency graph. It is
  context expansion only and never creates strong evidence.
- Explicit dotted identifiers use a lexer-like identifier pattern and ignore
  decimals, URLs, and email addresses.

Acceptance for this tranche is a focused adversarial test set plus the full
existing suite, with no arbitrary-table fallback when all evidence is weak.
