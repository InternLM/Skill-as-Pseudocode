<!-- refactored skeleton for anthropic_alfworld-heat-object-with-appliance (3 of 6 units replaced by child invocations (cleanup applied)) -->

# Skill: Heat Object with Appliance

## Purpose
invoke(appliance-state-modifier, {object="specified object", appliance="designated kitchen appliance", action="heat"})

## Core Workflow
invoke(appliance-state-modifier, {object="object", appliance="heating appliance", action="heat"})  (parent-specific: Follow this sequence of actions. Use the bundled `validate_and_plan.py` script to check for common preconditions before…)

## Key Decision Points
*   **Appliance State:** If the appliance is occupied, assess if the task implies clearing it. The trajectory shows proceeding without removal is sometimes valid.
*   **Object Location:** The object may not be at the initial location. Be prepared to search other receptacles (e.g., fridge, countertop, cabinet) if not found.
*   **Alternative Appliances:** If the primary appliance (e.g., microwave) is unavailable or broken, consider alternatives like a stoveburner.

## Example
invoke(appliance-state-modifier, {object="egg 1", appliance="microwave 1", action="heat"})  (parent-specific: put it on the diningtable. target: diningtable 1
1. `go to fridge 1` → Observation: "You are at fridge 1."
2. `open frid…)

## Bundled Resources
*   `scripts/validate_and_plan.py`: A utility to check the initial environment state against the skill's prerequisites.
*   `references/common_heating_appliances.md`: A list of typical appliances and their properties in the ALFWorld environment.