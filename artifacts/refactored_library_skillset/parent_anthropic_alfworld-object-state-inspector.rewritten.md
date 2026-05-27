<!-- refactored skeleton for anthropic_alfworld-object-state-inspector (1 of 4 units replaced by child invocations (cleanup applied)) -->

# Instructions
Inspect the state or contents of a target receptacle by navigating to it and parsing the environment's observation feedback.

## Workflow
invoke(locate-target-object, {target_object="item", suspected_receptacle="receptacle"})  (parent-specific: 1. **Navigate:** Execute `go to {target_receptacle}`
2. **Read observation:** The environment automatically reports what…)

## Error Recovery
- "Nothing happened": the `go to` target name is invalid -- verify the receptacle name from your environment scan
- This skill uses only `go to` for navigation; it does not use `open`, `close`, or `toggle`

## Example
**Scenario 1:** Check if a toiletpaperhanger has toilet paper.

```
Action: invoke(locate-target-object, {target_object="toilet paper", suspected_receptacle="toiletpaperhanger 1"})
Observation: On the toiletpaperhanger 1, you see nothing.
```

**Decision:** Holder is empty. Find a toiletpaper roll elsewhere and bring it here.

**Scenario 2:** Check a toilet for available items.

```
Action: invoke(locate-target-object, {target_object="items", suspected_receptacle="toilet 1"})
Observation: On the toilet 1, you see a soapbottle 1, and a toiletpaper 1.
```

**Decision:** toiletpaper 1 is available. Execute `take toiletpaper 1 from toilet 1`.