<!-- refactored skeleton for anthropic_alfworld-appliance-navigator (2 of 4 units replaced by child invocations (cleanup applied)) -->

# Instructions
invoke(appliance-state-modifier, {object="object that needs to be processed", appliance="correct appliance", action="heated, cooled, or cleaned"})

## Workflow
1. **Identify the Target Appliance:** Determine which appliance is required for the task. Map the action to the appliance: `heat` -> microwave/stoveburner, `cool` -> fridge, `clean` -> sinkbasin.
2. **Locate the Appliance:** Scan the environment observation for the target appliance (e.g., `microwave 1`, `fridge 1`, `sinkbasin 1`).
3. **Navigate:** Execute `go to {appliance}` to move to the identified appliance location.
4. **Prepare Appliance (if needed):** Upon arrival, check if the appliance requires preparation (e.g., opening a closed microwave or fridge door). If so, perform `open {appliance}` before proceeding.

## Example
invoke(appliance-state-modifier, {object="potato 1", appliance="microwave 1", action="heat"})  (parent-specific: Thought: I need to open the microwave before I can use it.)

## Key Principles
- **Trigger:** The agent is holding an object and the next step in the task is to `heat`, `cool`, or `clean` it.
- **Core Action:** The primary output of this skill is the navigation command `go to {target_appliance}`.
- **Prerequisite Check:** Always ensure the appliance is accessible (e.g., open) before attempting to use it for processing.