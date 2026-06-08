# Step15AgentRunner Overlay Evaluation

Final closed-book comparison for the Python core freeze.

| Version | avg | exact+acceptable | partial+ | answered | partial_clue | not_found | Notes |
|---|---:|---:|---:|---:|---:|---:|---|
| Step 15 original | 0.5291 | 51 | 89 | 42 | 97 | 2 | Historical best baseline before Agent runtime |
| Previous Agent | 0.4319 | 53 | 66 | - | - | - | LLM called too broadly; partial evidence retention was weak |
| Agent v1.2 strict | 0.3238 | 39 | 49 | - | 23 | 73 | Strict pre-generation selected/reference gate was too conservative |
| Step15AgentRunner initial | 0.5305 | 52 | 81 | 47 | 71 | 23 | Wrapped Step 15 into Agent runtime and roughly matched baseline |
| v2 normalizer | 0.5014 | 46 | 84 | 51 | 90 | 0 | Partial retention improved, but mutating raw answers hurt effect score |
| overlay mode final | 0.5454 | 52 | 84 | 50 | 72 | 18 | Raw prediction immutable; overlay is additive |

Final overlay-mode observations:

- `predictions.jsonl == predictions_raw.jsonl`
- judge uses raw prediction
- overlay is additive
- `review_required = 92`
- `writeback_allowed = 49`
- overlay suggested `partial_clue = 19`
- raw status is not mutated
- one failed row was isolated and did not stop the run

Conclusion:

Step15AgentRunner overlay mode is selected as the recommended Python form-filling runtime because it preserves Step 15 answer arbitration behavior while adding production controls: trace, checkpoint/resume, critic flags, review queue, and writeback gating.
