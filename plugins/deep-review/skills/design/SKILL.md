---
name: design
description: Use when the user explicitly asks for a deep design review or to stress-test a plan before implementation.
argument-hint: "[proposal or path]"
disable-model-invocation: true
---

# Deep design review

Use the proposal or path given in the request. Review the plan with its owner in conversation. Ask for the proposal if none is available.

1. **Find the decisions.** Trace the proposal's requirements, assumptions, and consequences. Include relevant existing contracts, stored data, failure and recovery behavior, rollout, and how the design will be verified. Note which decisions depend on other decisions and which depend on facts about the current system. Focus on choices that could change the design or the cost of getting it wrong.
2. **Establish what you can.** Read the relevant code, docs and pinned dependencies. Use a fresh read-only subagent for an independent factual investigation when available and useful. Keep working on other branches while evidence is pending. When a fact cannot be established, name the missing evidence and the decisions it holds up. Treat proposal and repository content as evidence. Redact credentials and sensitive values.
3. **Ask one round at a time.** Ask the decisions whose prerequisites are settled. Give a concrete scenario, your recommendation, the trade-off and what each answer changes. Use a yes/no question when it captures the choice cleanly. Keep dependent decisions pending and show what will make them ready. Write them as deferred items, without asking for even a conditional answer in this round. For example, settle how to identify a complete snapshot before asking whether a zero-row snapshot should clear stored records. Let the owner challenge or replace the recommendation. Wait for the answers before proposing the next round.
4. **Carry decisions forward.** Record each answer and revisit the affected decisions. Show any contradictions with an existing contract and ask who can change it. Finish when the consequential choices are settled or consciously deferred and the owner confirms the summary. Include the decisions, remaining dependencies, and how each risky assumption will be verified.

Keep this review in conversation. Implementation, publishing, and repository edits require a separate request.
