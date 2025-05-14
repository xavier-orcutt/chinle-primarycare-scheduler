# Chinle Primary Care Scheduler 
This project is a custom-built Python tool to help automate and streamline the creation of outpatient clinic schedules for internal medicine, family practice, and pediatrics. It accounts for each provider’s schedule preferences, clinic workload limits, time off, inpatient assignments, and federal holidays.

The goal is to make the scheduling process more transparent, consistent, and timely. Ideally, this tool will allow the entire outpatient schedule to be generated shortly after the inpatient schedule and leave requests are finalized.

Currently, only the internal medicine scheduler is active. Versions for family practice and pediatrics are in development.

## Problem Overview
Consider a 5-day week with 9 total clinic sessions. If 5 providers are available and each clinic session can staff 3 providers,a rough upper bound on the number of combinations is:

$${\binom{5}{3}}^9 = 10^9 = 1,\000,\000,\000 \text{ possible schedules}$$

Even after applying just a single constraint, limiting each provider to a maximum of 6 sessions per week, a conservative estimate suggests that 10–20% of these combinations remain feasible, resulting in roughly 100–200 million valid schedules. When scaled across multiple departments and multiple weeks, the size of the scheduling space becomes astronomically large.

The real complexity, however, doesn’t lie in the sheer number of possible schedules, but in how tightly interwoven constraints like leave, RDOs, staffing minimums and maximums, and per-provider clinic caps—interact to restrict the feasible set.

To solve this efficiently, we use [CP-SAT](https://developers.google.com/optimization/cp), a powerful open-source solver developed by Google that’s designed for precisely this kind of constraint satisfaction problem. CP-SAT treats scheduling as a constraint satisfaction problem, searching systematically for solutions that meet all predefined rules. It is particularly well-suited to scheduling problems like this one because it can efficiently prune infeasible branches of the search space and handle both hard constraints and soft preferences.

## Inputs 
The model relies on three main inputs:

1. Calendar – the date range over which the schedule will be built (see `utils/calendar.py`)

2. Data – inpatient assignments and leave requests (see `data/inpatient.csv` and `data/leave_request.csv`)

3. Rules – codified constraints that define how providers can be scheduled

### Rules

The rules section deserves particular attention as it's the heart of the scheduling system. The scheduler enforces the core rules as defined in the `docs/clinic_rules.pdf` and `docs/call_rules.pdf`. 

General clinic and provider-specific rules are defined in the `config` folder and are imported and codified as constraints for the model in `constraints`.

The constraints are implemented as a combination of:
* Hard constraints that cannot be violated (e.g., providers cannot work clinic during inpatient)
* Soft constraints with penalties that guide the optimizer toward preferable solutions while maintaining flexibility when strict adherence isn't possible (e.g., ensuring that providers work as close as possible to their designated weekly clinic amount)

All constraints are considered simultaneously during solving. This approach ensures the scheduler can find workable solutions even when competing requirements make perfect solutions impossible. 

## Output

With these inputs, The CP-SAT model produces a binary output for each provider, day, and session:

- 1 = scheduled to work in clinic
- 0 = not scheduled (admin, RDO, inpatient, or leave) 

Clinic staffing can be determined by identifying which providers have a value of 1 for each day and session.

```yaml
Solution found in 0.000010 seconds
2025-08-04 Monday morning: staffed by ['Mcrae', 'Miles']
2025-08-04 Monday afternoon: staffed by ['Mcrae', 'Miles']
2025-08-05 Tuesday morning: staffed by ['Mcrae', 'Miles', 'Selig', 'Stewart', 'Tanay']
2025-08-05 Tuesday afternoon: staffed by ['Mcrae', 'Selig', 'Stewart', 'Tanay']
...
```

## Usage

A full walkthrough of how the internal medicine schedule was created for August 2025 can be found in `tutorial/tutorial.md`.

## Directory Architecture

```bash
├── engine/                        # Entry point for running the scheduler
│   └── engine.py
├── constraints/                   # Scheduling rules and constraint logic
│   └── internal_medicine.py
├── utils/                         # Input parsing and calendar creation
│   ├── parser.py
│   └── calendar.py
├── config/                        # Clinic and provider rules 
│   └── internal_medicine.yml
├── docs/                       
│   └── scheduler_rules.pdf        # Plain-English summary of scheduling rules
└── data/                          # Input data
    ├── inpatient.csv
    └── leave_requests.csv
```

## Requirements

Built and tested in python 3.13.

Core dependencies: 
- pandas
- pyyaml
- ortools
