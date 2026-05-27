<!-- refactored skeleton for anthropic_alfworld-location-navigator (1 of 4 units replaced by child invocations (cleanup applied)) -->

# Instructions
Use this skill to navigate to a specific location in the Alfworld environment. The skill will move the agent directly to the target receptacle or object.

## Input
invoke(alfworld-navigation)

## Process
1.  The skill validates the target location against the current environment observation.
2.  invoke(alfworld-navigation, { current_location, target_receptacle })
3.  It returns the environment's observation after the move.

## Notes
- Ensure the target location is visible in the agent's current observation before calling this skill.
- This skill is for navigation only. Use other skills for object interaction (take, put, etc.).
- If the action fails (e.g., "Nothing happened"), the agent may need to replan its path or verify the target name.