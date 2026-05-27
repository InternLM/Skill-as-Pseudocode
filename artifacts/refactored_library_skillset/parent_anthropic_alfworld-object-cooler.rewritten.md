<!-- refactored skeleton for anthropic_alfworld-object-cooler (3 of 7 units replaced by child invocations (cleanup applied)) -->

# Skill: Cool Held Object

## Purpose
invoke(appliance-state-modifier, {object="pot 1", appliance="fridge 1", action="cool"})

## Prerequisites
invoke(appliance-state-modifier, {object="pot 1", appliance="fridge 1", action="cool"})  (parent-specific: Use `go to {recep}` to navigate first if needed. The environment will validate this.)

## Core Action
invoke(appliance-state-modifier, {object="{obj}", appliance="cooling receptacle", action="cool"})  (parent-specific: 1.  **Verify State:** Confirm you are holding the object and are at the cooling receptacle's location. 3.  **Verify Outc…)

## Example from Trajectory
*   **State:** Holding `pot 1`, at `fridge 1`.
*   **Action:** invoke(appliance-state-modifier, {object="pot 1", appliance="fridge 1", action="cool"})
*   **Result:** Observation: "You cool the pot 1 using the fridge 1."

## Next Steps
After successful cooling, the object is ready for the next task step (e.g., `put {obj} in/on {recep}`).