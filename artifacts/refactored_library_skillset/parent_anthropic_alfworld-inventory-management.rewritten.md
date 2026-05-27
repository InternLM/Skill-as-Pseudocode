<!-- refactored skeleton for anthropic_alfworld-inventory-management (1 of 9 units replaced by child invocations (cleanup applied)) -->

# Inventory Management Skill

## When to Use
Activate this skill when:
- Task requires collecting **multiple instances** of the same object type (e.g., "put two cellphone in bed")
- You need to track progress toward a quantity-based goal
- Searching through multiple locations systematically

## Core Workflow

### 1. Initialize Inventory
- Parse the task description to identify:
  - **Target object type** (e.g., "cellphone")
  - **Required quantity** (e.g., "two" = 2)
  - **Target receptacle** (e.g., "bed 1")
- Initialize counters: `collected = 0`, `needed = <quantity>`
- Create empty list for searched locations

### 2. Systematic Search Pattern
Follow this search priority:
1. **Visible surfaces** (desks, dressers, beds, countertops) - check first
2. **Closed containers** (drawers, cabinets, safes) - open and inspect
3. **Less common locations** (shelves, side tables, garbage cans)
4. **Return to known locations** if inventory incomplete

**Critical Rule:** After finding an object, immediately place it at the target location before searching for the next one. Do not attempt to carry multiple objects simultaneously.

### 3. Action Decision Logic
Use this decision tree at each step:

```
Is target object visible in current observation?
├── YES → Take it, go to target receptacle, put it down
│         └── Increment collected counter
│             ├── collected == needed → TASK COMPLETE
│             └── collected < needed → Continue searching
└── NO → Have all receptacles been searched?
          ├── YES → Revisit receptacles (objects may have been missed)
          └── NO → Go to next unsearched receptacle
```

### 4. Per-Object Cycle
invoke(alfworld-object-management, {object="{object}", source_receptacle="{current_receptacle}", target_receptacle="{target_receptacle}"})  (parent-specific: Update counter: `collected += 1` If `collected < needed`, navigate to next unsearched receptacle)

## Example
**Task:** "Put two cellphone in bed 1."

```
> go to desk 1
On the desk 1, you see a cellphone 2, a pen 1.
> invoke(alfworld-object-management, {object="cellphone 2", source_receptacle="desk 1", target_receptacle="bed 1"})
[collected: 1/2]
> go to dresser 1
On the dresser 1, you see a cellphone 3, a keychain 1.
> invoke(alfworld-object-management, {object="cellphone 3", source_receptacle="dresser 1", target_receptacle="bed 1"})
[collected: 2/2 — TASK COMPLETE]
```

## Error Handling
- **Object not at expected location**: Mark location as searched, proceed to next receptacle
- **"Nothing happened"**: The action syntax may be wrong; verify object name and receptacle
- **Counter mismatch**: Re-examine the target receptacle to confirm how many objects are already placed