<!-- refactored skeleton for anthropic_alfworld-appliance-preparer (1 of 7 units replaced by child invocations (cleanup applied)) -->

# Instructions

## Goal
Prepare a specified household appliance for immediate use by ensuring it is in the correct open or closed state. This is a prerequisite step before performing actions like `heat`, `cool`, or `toggle` with the appliance.

## Input
- **appliance_identifier**: The name of the appliance to prepare (e.g., `microwave 1`, `toaster 1`, `fridge 1`).

## Core Logic
invoke(appliance-state-modifier, {object="item", appliance="microwave 1", action="open"})  (parent-specific: 1.  **Navigate to the Appliance**: First, go to the location of the target appliance.
2.  **Check State & Prepare**: Det…)

## Important Considerations
- **State Awareness**: Always observe the environment's feedback after each action (e.g., "The microwave 1 is closed."). Do not assume the state.
- **Error Handling**: If the action fails (environment outputs "Nothing happened"), the appliance may already be in the desired state. Re-check the observation and proceed.
- **Trajectory Insight**: Refer to the example in `references/trajectory_example.md` to see a practical application of this skill in the context of a larger task.

## Example
**Input:** `appliance_identifier: microwave 1`

**Sequence:**
1. `go to microwave 1` → Observation: "You are at microwave 1. The microwave 1 is closed."
2. invoke(appliance-state-modifier, {object="item", appliance="microwave 1", action="open"}) → Observation: "You open the microwave 1. The microwave 1 is open."

**Output:** "The microwave 1 is open and ready for use."

## Output
A confirmation that the appliance is ready, typically in the form of the agent's `Thought` summarizing the prepared state and the environment's observation.