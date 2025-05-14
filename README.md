# Chinle Primary Care Scheduler 
This project is a custom-built Python tool to help automate and streamline the creation of outpatient clinic schedules for internal medicine, family practice, and pediatrics. It accounts for each provider’s schedule preferences, clinic workload limits, time off, inpatient assignments, and federal holidays.

The goal is to make the scheduling process more transparent, consistent, and timely. Ideally, this tool will allow the entire outpatient schedule to be generated shortly after the inpatient schedule and leave requests are finalized.

Currently, the internal medicine and pediatric schedulers are active. A version for family practice is in development.

## Problem Overview
Consider a 5-day week with 9 total clinic sessions. If 5 providers are available per day on average and each clinic session can staff 3 providers, an upper bound on the number of schedule combinations is:

$${\binom{5}{3}}^9 = 10^9 = 1,000,000,000$$

Even after applying just a single constraint, limiting each provider to a maximum of 6 sessions per week, a conservative estimate suggests that 10–20% of these combinations remain feasible, resulting in roughly 100–200 million valid schedules. When scaled across multiple departments and multiple months, the size of the scheduling space becomes astronomically large.

The real complexity, however, doesn’t lie in the sheer number of possible schedules, but in how tightly interwoven constraints like leave, RDOs, staffing minimums and maximums, and per-provider clinic caps interact to restrict the feasible set.

To solve this efficiently, we use [CP-SAT](https://developers.google.com/optimization/cp), an open-source solver developed by Google that’s designed for precisely this kind of constraint satisfaction problem. CP-SAT is particularly well-suited to scheduling problems like this one because it can efficiently prune infeasible branches of the search space and handle both hard constraints and soft preferences.

## Inputs 
The model relies on three main inputs:

1. Calendar – the date range over which the schedule will be built (see `utils/calendar.py`)

2. Data – inpatient assignments and leave requests (see `data/inpatient.csv` and `data/leave_request.csv`)

3. Rules – codified constraints that define how providers can be scheduled

### Rules

The rules section deserves particular attention as it's the heart of the scheduling system. The scheduler enforces the core rules as defined in the `docs/clinic_rules.pdf` and `docs/call_rules.pdf`. 

General clinic and provider-specific rules are defined in the `config` folder and are imported and codified as constraints for the model in `constraints`.

The constraints are implemented as a combination of:
* **Hard constraints** that cannot be violated (e.g., providers cannot work clinic during inpatient)
* **Soft constraints** with penalties that guide the optimizer toward preferable solutions while maintaining flexibility when strict adherence isn't possible (e.g., ensuring that providers work as close as possible to their designated weekly clinic amount)

All constraints are considered simultaneously during solving. This approach ensures the scheduler can find workable solutions even when competing requirements make perfect solutions impossible. 

## Scheduling Process
The scheduling process follows these key steps:

1. **Input Processing**: The system takes in the inputs as defined above.
2. **Adaptive Minimum Staffing**: The scheduler automatically determines the highest possible minimum staffing level that allows for a feasible schedule. It starts with an initial target of 4 providers and systematically reduces this value until a viable schedule is found.
3. **Constraint Satisfaction**: The CP-SAT solver applies all hard constraints (like inpatient assignments) while minimizing penalties from soft constraints (like minimizing more than 1 pediatric call a week).

The system treats all submitted leave requests as pre-approved during the scheduling process and automatically identifies the highest achievable minimum staffing level given the leave requests. This provides a clear picture of the "worst-case" staffing scenario if all requested leave were granted. Final leave approval remains at the discretion of department chiefs.

## Interpreting Scheduler Output

The CP-SAT model produces a binary output for each provider, day, and session:

- 1 = scheduled to work in clinic or call
- 0 = not scheduled (ie., admin, RDO, inpatient, or leave) 

This binary decision matrix is then translated into user-friendly outputs which provide detailed information about the quality of the scheduling solution.

### Schedule Dataframe
The primary output is the complete schedule showing which providers are assigned to each session:

```bash
date	    day_of_week   session	    providers	   count
2025-08-04	Monday	      morning	    House,Watson   2
2025-08-04	Monday	      afternoon	    House,Watson   2        
```

### Provider Summary Dataframe
A summary of each provider's workload is also generated:

```bash
provider	week_32	week_33	week_34	week_35	total_sessions
Spaceman	0	    2	    4	    4	    10
House	    6	    5	    6	    6	    23
Watson   	3	    3	    0	    0	    6
```

### Solution Status 
Detailed information about the scheduling process and solution quality:

```bash
{'Status': 'OPTIMAL',
 'Minimum providers per session': 2,
 'Objective value': 4100.0,
 'Solve time': '0.022486 seconds',
 'Branches': 640,
 'Conflicts': 0}
 ```
 
When the scheduler completes, it categorizes the solution as either:
* OPTIMAL: The scheduler has proven this is the best possible solution given the constraints.
* FEASIBLE: The scheduler found a valid solution that satisfies all hard constraints but couldn't prove it's optimal within the time limit, which is currently set to 5 minutes.

Both solution types are valid and usable for implementation, though optimal solutions guarantee no better alternative exists.

The objective value represents the total penalty from soft constraint violations. A lower value indicates a better schedule. Common penalties include:
* Providers not meeting target clinic sessions
* A provider on pediatric call multiple times in a week 

## Usage

A walkthrough of how the internal medicine and pediatric schedules were created for August 2025 can be found in `notebooks/august_schedule.ipynb`.

## Directory Architecture

```bash
├── engine/                        # Entry point for running the scheduler
│   └── engine.py
├── constraints/                   # Scheduling rules and constraint logic
│   ├── internal_medicine.py
│   └── pediatric.py
├── utils/                         # Input parsing and calendar creation
│   ├── parser.py
│   └── calendar.py
├── config/                        # Clinic and provider rules 
│   ├── internal_medicine.yml
│   └── pediatric.yml    
├── docs/                          # Plain-English summary of scheduling rules     
│   ├── clinic_rules.pdf
│   └── call_rules.pdf     
├── data/                          # Input data
│   ├── inpatient.csv
│   └── leave_requests.csv
└── notebooks/                     # Collection of Jupyter notebooks
    └── august_schedule.ipynb    

```

## Requirements

Built and tested in python 3.13.

Core dependencies: 
- pandas
- pyyaml
- ortools
