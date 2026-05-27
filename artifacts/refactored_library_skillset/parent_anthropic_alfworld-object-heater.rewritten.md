<!-- refactored skeleton for anthropic_alfworld-object-heater (4 of 6 units replaced by child invocations (cleanup applied)) -->

# Instructions
invoke(appliance-state-modifier, {object="an object you are holding", appliance="microwave or stoveburner", action="heat"})

## Prerequisites
- The target object must be in your inventory
- A heating appliance (microwave, stoveburner) must exist in the environment

## Workflow
invoke(appliance-state-modifier, {object="{object}", appliance="{appliance}", action="heat"})  (parent-specific: 1. **Navigate:** `go to {appliance}` (e.g., `go to microwave 1`)
2. **Check state:** If observation says appliance is cl…)

## Action Format
invoke(appliance-state-modifier, {object="potato 1", appliance="microwave 1", action="heat"})  (parent-specific: `go to microwave 1` / `go to stoveburner 1`
- `open microwave 1`)

## Error Recovery
- "Nothing happened" on heat: Check (1) you are holding the object, (2) appliance is open, (3) appliance name is correct
- Appliance occupied: Take the existing item out first, then retry

## Example
invoke(appliance-state-modifier, {object="potato 1", appliance="microwave 1", action="heat"})  (parent-specific: Proceed to place it at the destination if required by the task.)