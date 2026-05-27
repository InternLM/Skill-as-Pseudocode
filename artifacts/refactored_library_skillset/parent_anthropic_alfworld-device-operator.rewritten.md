<!-- refactored skeleton for anthropic_alfworld-device-operator (1 of 9 units replaced by child invocations (cleanup applied)) -->

# Instructions

## 1. Skill Trigger
Activate this skill when the task goal explicitly requires **using a device or appliance** to interact with a target object. Common indicators include phrases like:
- "look at [object] under the [device]"
- "heat [object] with [device]"
- "cool [object] with [device]"
- "clean [object] with [device]"

## 2. Core Execution Flow
Follow this sequence when the skill is triggered:

### Phase 1: Locate the Device
1.  **Identify the device** from the task description (e.g., `desklamp`, `microwave`, `fridge`).
2.  **Search common receptacles** where such a device is typically found (e.g., desks, sidetables, countertops).
3.  Use the `go to {recep}` action to navigate to and inspect these locations until the device is found.
4.  **Note the device's exact name** (e.g., `desklamp 1`).

### Phase 2: Locate the Target Object
1.  **Identify the target object** from the task description (e.g., `laptop`, `mug`, `plate`).
2.  **Search the environment** for this object. It may not be near the device initially.
3.  Use the `go to {recep}` and visual inspection to find the object.
4.  Once found, use `take {obj} from {recep}` to pick it up.

### Phase 3: Co-locate Object and Device
1.  **Navigate** to the receptacle where the target device is located using `go to {recep}`.
2.  Ensure you are in the same location as the device before proceeding.

### Phase 4: Operate the Device
invoke(appliance-state-modifier, {object="{obj}", appliance="{device}", action="heat"})  (parent-specific: Execute the final **use action**. The specific action is determined by the device-object pair: * For a `desklamp` and a…)

## 3. Key Assumptions & Rules
*   **Co-location Required:** The skill assumes the final action requires the target object and the operating device to be in the same location (on the same receptacle).
*   **Device First:** Prioritize finding the device before extensively searching for the target object, as the device's location is often a fixed landmark.
*   **Invalid Actions:** If the environment responds with "Nothing happened," re-evaluate your object/device names and your location. Ensure you are using the correct action verb for the device type.

## Example
**Scenario:** Task is "look at laptop under the desklamp."


```
Thought: I need to find the desklamp first. It's likely on a sidetable or desk.
Action: go to sidetable 1
Observation: On the sidetable 1, you see a desklamp 1.
Thought: Found the desklamp. Now I need to find the laptop.
Action: go to desk 1
Observation: On the desk 1, you see a laptop 1, a pen 2.
Action: take laptop 1 from desk 1
Observation: You pick up the laptop 1 from the desk 1.
Action: go to sidetable 1
Observation: On the sidetable 1, you see a desklamp 1.
Action: invoke(appliance-state-modifier, {object="laptop 1", appliance="desklamp 1", action="heat"})
```

**Result:** The laptop is now being examined under the desklamp, completing the task.