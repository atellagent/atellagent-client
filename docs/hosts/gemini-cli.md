# Gemini CLI capability — deferred

Gemini CLI exposes command-hook checkpoints including `BeforeModel` and
`BeforeTool`. Its public hook reference also exposes `AfterModel`, but
Atellagent does not implement an `AfterModel` response checkpoint, response
rewriting, or response redaction in this plan.

No Atellagent Gemini CLI adapter, installation template, identity binding, or
coverage claim is currently provided. Do not infer that a generic local hook
control configuration works with Gemini CLI. A future implementation requires a
separate host-contract review, explicit service-account boundary design, and
coverage tests before it can be documented as supported.
