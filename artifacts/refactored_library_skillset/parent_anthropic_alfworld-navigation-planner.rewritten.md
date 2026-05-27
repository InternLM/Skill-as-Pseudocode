<!-- refactored skeleton for anthropic_alfworld-navigation-planner (1 of 4 units replaced by child invocations (cleanup applied)) -->

# Instructions
Use this skill to navigate between receptacles (e.g., bed, desk, drawer) in a household environment to locate objects. The core logic is handled by the bundled script.

## Input/Output Format
invoke(alfworld-navigation, {current_location="the agent's current location", target_receptacle="the target receptacle"})

## How to Use
1.  **Identify your goal.** Determine which receptacle you need to search (e.g., `desk 1` to find a `desklamp`).
2.  **Call the skill.** invoke(alfworld-navigation, {current_location="the agent's current location", target_receptacle="the target receptacle"})
3.  **Execute the action.** Perform the action returned by the skill.
4.  **Observe and repeat.** After moving, observe the new location. If the target object is not found, use this skill again to plan movement to the next most promising receptacle.

## Key Principles
- **Systematic Search:** Move efficiently between likely receptacles instead of random exploration.
- **Adaptive Planning:** If an action fails ("Nothing happened"), the script's logic helps choose an alternative path.
- **Context Preservation:** Always note your current location after each move for the next planning step.

**Example Thought Process:**
> Thought: I need to find a desklamp. The `desk 1` is a likely location. I am currently at `bed 1`.
> Action: invoke(alfworld-navigation, {current_location="bed 1", target_receptacle="desk 1"})
> Result: `go to desk 1`