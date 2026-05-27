<!-- refactored skeleton for anthropic_alfworld-locate-target-object (3 of 7 units replaced by child invocations (cleanup applied)) -->

# Skill: Locate Target Object

## Purpose
invoke(locate-target-object, {target_object="specific object", suspected_receptacle="likely receptacle"})

## Core Workflow
invoke(locate-target-object, {target_object="the object you need", suspected_receptacle="the most logical place to find it"})

## Instructions
invoke(locate-target-object, {target_object="<target_object>", suspected_receptacle="<suspected_receptacle>"})

## Key Principles
*   **Contextual Guessing:** Use common sense to guess the initial search location (e.g., `fridge` for perishables, `cabinet` for dry goods).
*   **Sequential Access:** You must `go to` a location before you can `open` it.
*   **Visual Confirmation:** Only trust the `Observation` after opening a receptacle to confirm an object's presence or absence.

## Example
**Scenario:** You need to find a potato for a heating task.

```
Thought: I need to find a potato. Potatoes are commonly stored in the fridge.
Action: invoke(locate-target-object, {target_object="potato", suspected_receptacle="fridge 1"})
```

**Result:** The potato has been located in `fridge 1`. You can now `take potato 1 from fridge 1` and proceed.

## Next Steps
After successfully locating the object, you will typically need to `take` it or interact with it, which is outside the scope of this skill. If the object is not found, trigger this skill again with a new suspected location.