<!-- refactored skeleton for anthropic_alfworld-temperature-regulator (3 of 6 units replaced by child invocations (cleanup applied)) -->

# Instructions
This skill executes a sequence to change an object's temperature by placing it in a specific receptacle (e.g., fridge for cooling, microwave for heating) and then relocating it to a final target location.

## 1. Input Validation & Planning
invoke(cool-object-with-appliance, {object="object identifier (e.g., bread 1)", temperature_receptacle="temperature_receptacle identifier (e.g., fridge 1)", target_receptacle="final target_receptacle (e.g., diningtable 1)"})  (parent-specific: Verify the provided object and receptacles exist in the agent's current observation. If not, the agent must first naviga…)

## 2. Execution Sequence
invoke(cool-object-with-appliance, {object="{object}", temperature_receptacle="{temperature_receptacle}", target_receptacle="{target_receptacle}"})  (parent-specific: Follow this core logic. Use deterministic scripts for error-prone steps (see `scripts/`). 1.  **Acquire Object:** `go to…)

## 3. Error Handling & Observations
*   If an action results in "Nothing happened", consult the troubleshooting guide in `references/troubleshooting.md`.
*   Always verify the state change after each action (e.g., "You pick up...", "You open...", "You put...").
*   If the object is not at the expected location, pause execution and re-scan the environment.

## 4. Example
invoke(cool-object-with-appliance, {object="bread 1", temperature_receptacle="fridge 1", target_receptacle="diningtable 1"})  (parent-specific: Task: "Cool some bread and put it on the diningtable."

**Sequence:**
1. `go to countertop 1` → Observation: "You are at…)

## 5. Completion
The skill is complete when the object has been placed into the `temperature_receptacle` and subsequently placed onto the `target_receptacle`. Confirm the final observation states the object is on the target.