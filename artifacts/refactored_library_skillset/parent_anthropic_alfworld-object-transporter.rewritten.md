<!-- refactored skeleton for anthropic_alfworld-object-transporter (1 of 6 units replaced by child invocations (cleanup applied)) -->

# Instructions
Pick up an object from its current location and transport it to a destination receptacle.

## Workflow
1. **Navigate to source:** `go to {source_receptacle}` -- verify observation shows the target object
2. **Pick up:** invoke(alfworld-object-management, {object="{object}", source_receptacle="{source_receptacle}", target_receptacle="{target_receptacle}"}) 
3. **Navigate to destination:** `go to {target_receptacle}`
4. **Place:** invoke(alfworld-object-management, {object="{object}", source_receptacle="{source_receptacle}", target_receptacle="{target_receptacle}"}) 

## Action Format
invoke(alfworld-object-management, {object="{object}", source_receptacle="{receptacle}", target_receptacle="{receptacle}"})  (parent-specific: go to {receptacle})

## Error Recovery
- "Nothing happened" on take: verify you are at the correct receptacle and the object name matches the observation
- "Nothing happened" on put: verify you are holding the object and at the correct destination
- Object not visible: re-scan the environment to locate it before retrying

## Example
**Scenario:** Move `laptop 1` from `bed 2` to `desk 1`.

```
Action: go to bed 2
Observation: On the bed 2, you see a laptop 1, a pillow 1.
Action: invoke(alfworld-object-management, {object="laptop 1", source_receptacle="bed 2", target_receptacle="desk 1"})
Observation: You pick up the laptop 1 from the bed 2.
Action: go to desk 1
Observation: On the desk 1, you see a pen 2.
Action: invoke(alfworld-object-management, {object="laptop 1", source_receptacle="bed 2", target_receptacle="desk 1"})
Observation: You put the laptop 1 in/on the desk 1.
```

**Result:** The laptop has been transported from the bed to the desk.

## Bundled Resources
- **Script**: `scripts/transport_sequence.py` provides a deterministic sequence generator.
- **Reference**: `references/troubleshooting.md` contains common failure patterns and solutions.