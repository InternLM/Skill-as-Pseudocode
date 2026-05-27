<!-- refactored skeleton for anthropic_alfworld-object-picker (2 of 4 units replaced by child invocations (cleanup applied)) -->

# Instructions
Execute the `take` action to acquire the target object from the source receptacle.

## Inputs
invoke(alfworld-object-management)

## Process
1.  **Verify Context:** Ensure the agent is at the location of the `source_receptacle`. If not, the agent must navigate there first using a separate movement skill.
2.  **Execute Action:** invoke(alfworld-object-management, { object, source_receptacle, target_receptacle })
3.  **Handle Feedback:** If the environment indicates "Nothing happened," consult the troubleshooting guide in `references/troubleshooting.md` for potential issues.

## Output
invoke(alfworld-object-management)