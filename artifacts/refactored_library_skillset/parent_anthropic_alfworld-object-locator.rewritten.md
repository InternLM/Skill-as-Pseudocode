<!-- refactored skeleton for anthropic_alfworld-object-locator (2 of 9 units replaced by child invocations (cleanup applied)) -->

# Skill: Object Locator for ALFWorld

## When to Use
Trigger this skill when:
1. Your goal requires a specific object (e.g., `knife`, `cellphone`, `apple`)
2. The object is not in your inventory
3. The current observation does not explicitly state the object's location

## Core Workflow

### 1. Parse the Environment
Extract all visible receptacles from the observation text. Typical ALFWorld receptacles include:
- **Surfaces**: countertop, desk, dresser, bed, shelf, sidetable, coffeetable, diningtable
- **Containers**: drawer, cabinet, safe, fridge, microwave, garbagecan
- **Appliances**: sinkbasin, bathtub, stoveburner, toaster

### 2. Rank by Likelihood
invoke(alfworld-locate-object, {target_object="potato"}) 

### 3. Navigate and Search
invoke(locate-target-object, {target_object="{object}", suspected_receptacle="{receptacle}"}) 

### 4. Track Searched Locations
Maintain a list of already-searched receptacles to avoid revisiting them. If all high-probability locations are exhausted, expand the search to remaining receptacles.

## Example
**Task:** "Clean the knife and put it in drawer."
**Observation:** "You are in the middle of a room. Looking quickly around you, you see a countertop 1, a drawer 1, a drawer 2, a fridge 1, a sinkbasin 1, a stoveburner 1."

```
> go to countertop 1
On the countertop 1, you see a knife 1, a saltshaker 2, a bread 1.
> take knife 1 from countertop 1
You pick up the knife 1 from the countertop 1.
```

**Result:** Target object `knife 1` located and acquired from `countertop 1`.

**Example — Object not at first location:**

```
> go to countertop 1
On the countertop 1, you see a saltshaker 2, a bread 1.
> go to drawer 1
The drawer 1 is closed.
> open drawer 1
You open the drawer 1. The drawer 1 is open. In it, you see a knife 1.
> take knife 1 from drawer 1
You pick up the knife 1 from the drawer 1.
```

## Error Handling
- **Object not found in any receptacle**: Re-check closed containers that may not have been opened. Some objects are only visible after opening.
- **Multiple instances**: If the task requires a specific instance (e.g., `knife 1` vs `knife 2`), verify the object identifier matches before taking it.
- **"Nothing happened"**: The `take` command may fail if the agent is not at the receptacle. Ensure navigation was successful before attempting to take.