# Chinle Primary Care Scheduler 

This project is a custom-built Python tool to help automate and streamline the creation of outpatient clinic schedules for Internal Medicine, Family Practice, and Pediatrics. It accounts for each provider’s schedule preferences, clinic workload limits, time off, inpatient assignments, and federal holidays.

The goal is to make the scheduling process more transparent, consistent, and timely. Ideally, this tool will allow the entire outpatient schedule to be generated shortly after the inpatient schedule and leave requests are finalized.

## Problem Overview

Consider a 5-day week with 9 total clinic sessions. If 5 providers are available per day on average and each clinic session can staff 3 providers, an upper bound on the number of schedule combinations is:

$${\binom{5}{3}}^9 = 10^9 = 1,000,000,000$$

Even after applying just a single constraint, limiting each provider to a maximum of 6 sessions per week, a conservative estimate suggests that 10–20% of these combinations remain feasible, resulting in roughly 100–200 million valid schedules. When scaled across multiple departments and multiple months, the size of the scheduling space becomes astronomically large.

The real complexity, however, doesn’t lie in the sheer number of possible schedules, but in how tightly interwoven constraints like leave, RDOs, staffing minimums and maximums, and per-provider clinic caps interact to restrict the feasible set.

To solve this efficiently, we use [CP-SAT](https://developers.google.com/optimization/cp), an open-source solver developed by Google that’s designed for constraint satisfaction problems. CP-SAT is particularly well-suited to scheduling problems like this one because it can efficiently prune infeasible branches of the search space and handle both hard constraints and soft preferences. 

## How Does It Work? 

CP-SAT is a portfolio solver, meaning it runs multiple diverse algorithms at the same time. Each algorithm has its own strengths and weaknesses, allowing the solver to tackle different aspects of the problem effectively. These algorithms communicate and share information with each other, such as improved solutions or more efficient search spaces, helping the solver converge more quickly on an optimal solution.

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

All constraints are considered simultaneously by CP-SAT during solving. This approach ensures the scheduler can find workable solutions even when competing requirements make perfect solutions impossible. 

## Scheduling Process
The scheduling process follows these key steps:

1. **Input Processing**: The system takes in the inputs as defined above.
2. **Adaptive Minimum Staffing**: The scheduler automatically determines the highest possible minimum staffing level that allows for a feasible schedule. It starts with an initial target of 4 providers and systematically reduces this value until a viable schedule is found.
3. **Constraint Satisfaction**: The CP-SAT solver applies all hard constraints (like inpatient assignments) while minimizing penalties from soft constraints (like avoiding multiple pediatric calls in a week).

The system treats all submitted leave requests as pre-approved during the scheduling process and automatically identifies the highest achievable minimum staffing level given the leave requests. This provides a clear picture of the "worst-case" staffing scenario if all requested leave were granted. Final leave approval remains at the discretion of department chiefs.

## Cross-Department Scheduling 

Some primary care providers at Chinle serve in multiple departments, for example, working clinic in Family Practice while also taking call or clinic shifts in Pediatrics. To manage this complexity, the scheduler is run sequentially by department in a way that accounts for interdependencies:

* **Pediatrics First**: The Pediatrics schedule is generated first. This determines call and clinic assignments for all providers involved in Pediatric coverage, including those who also work in other departments.

* **Internal Medicine and Family Practice Next**: Once the Pediatrics schedule is finalized, it is used to block off corresponding dates for dual-role providers. These constraints are then incorporated into the Internal Medicine and Family Practice scheduling runs to ensure providers aren’t double-booked or overcommitted.

This staged approach ensures that cross-department providers are scheduled consistently and without conflict across the three services. It also reflects the higher coordination demands of Pediatrics, where call coverage is tightly structured and less flexible than general clinic staffing.

## Interpreting Scheduler Output

The CP-SAT model produces a binary output for each provider, day, and session:

- 1 = scheduled to work in clinic or call
- 0 = not scheduled (ie., admin, RDO, inpatient, or leave) 

This binary decision matrix is then translated into user-friendly outputs which provide detailed information about the quality of the scheduling solution.

### Schedule Dataframe
The primary output is the complete schedule showing which providers are assigned to each session:

|      date     | day_of_week |  session  |      providers      | count |
|---------------|-------------|-----------|---------------------|-------|
| 2025-08-04    | Monday      | morning   | House,Spaceman      |   2   |
| 2025-08-04    | Monday      | afternoon | Evil,House          |   2   |

### Provider Summary Dataframe
A summary of each provider's clinic workload is also generated:

|  provider  | week_31 | week_32 | week_33 | week_34 | total_sessions |
|------------|---------|---------|---------|---------|----------------|
| Evil       |    6    |    2    |    2    |    4    |       14       |
| House      |    6    |    5    |    6    |    6    |       23       |
| Spaceman   |    3    |    6    |    0    |    3    |       12       |

### Solution Status 
Detailed information about the scheduling process and solution quality:

```python
{'Status': 'OPTIMAL',
 'Minimum providers per session': 2,
 'Objective value': 4100.0,
 'Solve time': 0.022486 seconds,
 'Branches': 640,
 'Conflicts': 0}
 ```

When the scheduler completes, it categorizes the solution as either:
* **OPTIMAL**: The scheduler has proven this is the best possible solution given the constraints.
* **FEASIBLE**: The scheduler found a valid solution that satisfies all hard constraints but couldn't prove it's optimal within the time limit, which is currently set to 5 minutes.

Both solution types are valid and usable for implementation, though optimal solutions guarantee no better alternative exists.

The objective value represents the total penalty from soft constraint violations. A lower value indicates a better schedule. Common penalties include:
* Providers not meeting target clinic sessions
* A provider on pediatric call multiple times in a week 
* RDO assigned the day after call

## Usage

A walkthrough of how the internal medicine, family practice, and pediatric schedules were created for August 2025 can be found in `notebooks/august_schedule.ipynb`.

## Directory Architecture

```bash
├── config/                        # Clinic and provider rules 
│   ├── family_medicine.yml
│   ├── internal_medicine.yml
│   └── pediatric.yml
├── constraints/                   # Scheduling rules and constraint logic
│   ├── family_medicine.py
│   ├── internal_medicine.yml
│   └── pediatric.py
├── data/                          # Input data
│   ├── inpatient.csv
│   └── leave_requests.csv
├── docs/                          # Plain-English summary of scheduling rules     
│   ├── clinic_rules.pdf
│   └── call_rules.pdf     
├── engine/                        # Entry point for running the scheduler
│   └── engine.py
├── notebooks/                     # Collection of Jupyter notebooks
│   └── august_schedule.ipynb
├── output/                        # Schedule outputs saved as CSV files
│   ├── fp_schedule_df.csv
│   ├── im_schedule_df.csv
│   └── peds_schedule_df.csv
└── utils/                         # Input parsing and calendar creation
    ├── parser.py
    └── calendar.py
```

## Requirements

Built and tested in python 3.13.

Core dependencies: 
- pandas
- numpy
- pyyaml
- ortools
