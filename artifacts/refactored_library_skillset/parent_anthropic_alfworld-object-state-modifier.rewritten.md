<!-- refactored skeleton for anthropic_alfworld-object-state-modifier (3 of 5 units replaced by child invocations (cleanup applied)) -->

# Instructions
invoke(appliance-state-modifier, {object="target object", appliance="household appliance", action="cool, heat, or clean"})

## Workflow
invoke(appliance-state-modifier, {object="{obj}", appliance="{appliance}", action="{action}"})  (parent-specific: 1. **Navigate to appliance:** `go to {appliance}` (e.g., `go to fridge 1`)
2. **Prepare if closed:** If observation says…)

## Action Mapping
invoke(appliance-state-modifier, {object="{obj}", appliance="{appliance}", action="cool"})  (parent-specific: | State Change | Action | Typical Appliance |
|---|---|---|
| Heat | `heat {obj} with {appliance}` | `microwave 1`, `sto…)

## Error Recovery
- "Nothing happened": Check (1) you are holding the object, (2) appliance is open/ready, (3) object and appliance names are correct
- Appliance occupied: remove existing items first with `take {item} from {appliance}`

## Example
**Scenario:** You need to cool a tomato using the fridge.

```
Thought: I'm holding tomato 1 and need to cool it. I'll go to the fridge.
Action: go to fridge 1
Observation: The fridge 1 is closed.
Action: open fridge 1
Observation: You open the fridge 1. The fridge 1 is open. In it, you see a lettuce 1.
Action: invoke(appliance-state-modifier, {object="tomato 1", appliance="fridge 1", action="cool"})
Observation: You cool the tomato 1 using the fridge 1.
```

**Result:** The tomato is now in a cooled state. Proceed with the next task step (e.g., placing it on a countertop).