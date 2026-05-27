<!-- refactored skeleton for anthropic_alfworld-clean-object (2 of 6 units replaced by child invocations (cleanup applied)) -->

# Instructions
invoke(clean-object, {object="an object you are holding", receptacle="sinkbasin"})  (parent-specific: The object must be in your inventory before cleaning.)

## Workflow
invoke(clean-object, {object="{object}", receptacle="sinkbasin 1"})  (parent-specific: 1. **Navigate:** `go to sinkbasin 1` (or the appropriate sinkbasin in the environment))

## Action Format
- `clean {obj} with {recep}` (e.g., `clean potato 1 with sinkbasin 1`)

## Error Recovery
- "Nothing happened": Check (1) you are holding the object, (2) you are at the sinkbasin, (3) object and receptacle names are correct
- Not at sinkbasin: execute `go to sinkbasin 1` first

## Example
**Scenario:** You are holding `potato 1` and need to clean it.

```
Thought: I need to clean this potato. I should go to the sinkbasin.
Action: go to sinkbasin 1
Observation: On the sinkbasin 1, you see nothing.
Action: invoke(clean-object, {object="potato 1", receptacle="sinkbasin 1"})
Observation: You clean the potato 1 using the sinkbasin 1.
```

**Result:** The potato is now in a clean state and ready for the next task step.

## Post-Condition
After successful execution, the object will be in a clean state. You may proceed with the next step of your task (e.g., placing the clean object on a shelf or in a microwave).