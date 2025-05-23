# Chinle Primary Care Scheduler 

The scheduler is a custom-built Python tool to help automate and streamline the creation of outpatient clinic schedules for Internal Medicine, Family Practice, and Pediatrics. 

The goal is to make the scheduling process more transparent, consistent, and timely. Ideally, this tool will allow the entire outpatient schedule to be generated shortly after the inpatient schedule and leave requests are finalized.

## Problem Overview

Consider a 5-day week with 9 total clinic sessions. If 5 providers are available per day on average and each clinic session can staff 3 providers, an upper bound on the number of schedule combinations is:

$${\binom{5}{3}}^9 = 10^9 = 1,000,000,000$$

Even after applying just a single constraint, limiting each provider to a maximum of 6 sessions per week, a conservative estimate suggests that 10–20% of these combinations remain feasible, resulting in roughly 100–200 million valid schedules. When scaled dacross multiple departments and multiple months, the size of the scheduling space becomes astronomically large.

The real complexity, however, doesn’t lie in the sheer number of possible schedules, but in how tightly interwoven constraints like leave, RDOs, staffing minimums and maximums, and per-provider clinic caps interact to restrict the feasible set.

To solve this efficiently, the scheduler uses [CP-SAT](https://developers.google.com/optimization/cp), an open-source solver developed by Google that’s designed for constraint satisfaction problems. CP-SAT is particularly well-suited to scheduling problems like ours because it can efficiently handle both hard constraints and soft preferences. 

## How the Scheduler Works 

The Chinle Primary Care Scheduler is built in two layers:

### 1. The Scheduling Software 
The scheduling software converts the primary care department clinic rules into computer logic. It accounts for each provider’s schedule preferences, clinic workload limits, time off, inpatient assignments, and federal holidays.

### 2. The Optimization Engine (CP-SAT)
The scheduling software uses CP-SAT as its "brain" for solving the scheduling puzzle. CP-SAT is able to try millions of scheduling combinations in seconds, remember all rules, and find the best solution among all the valid possibilities. 

## Scheduler Inputs 

The scheduler relies on three main inputs:

1. **Calendar** – the date range over which the schedule will be built (see `utils/calendar.py`).
2. **Data** – inpatient assignments and leave requests (see `data/inpatient.csv` and `data/leave_requests.csv`).
3. **Rules** – codified constraints that define how providers can be scheduled.

### Rules

The rules section deserves particular attention as it's the heart of the scheduling system. The scheduler enforces the core rules through a three-step process:

1. **Plain-English Documentation** - Core scheduling policies are documented in `docs/clinic_rules.pdf` and `docs/call_rules.pdf`. 
2. **Configuration Files** - These policies are codified in human-readable YAML files (`config/` folder) containing both clinic-level rules (staffing minimums, holiday dates) and provider-specific rules (workload limits, RDO preferences). This structure makes it easy to adjust existing constraints without programming knowledge, add new providers or modify existing provider parameters, and maintain separate rule sets for each department.
3. **Mathematical Constraints** - The YAML rules are translated into mathematical constraints for the CP-SAT model in the `constraints/` folder.

    The mathematical constraints are implemented as a combination of:
    * **Hard constraints** that cannot be violated (e.g., providers cannot work clinic during inpatient).
    * **Soft constraints** with penalties that guide the optimizer toward preferable solutions while maintaining flexibility when strict adherence isn't possible (e.g., ensuring that providers work as close as possible to their designated weekly clinic amount).

    All constraints are considered simultaneously by CP-SAT during solving. This approach ensures the scheduler can find workable solutions even when competing requirements make perfect solutions impossible.

## Schedule Generation

Each department (Internal Medicine, Family Practice, and Pediatrics) generates its own schedule. The schedule for each department is created through these key steps:

1. **Input Processing**: The scheduler takes in the inputs as defined above.
2. **Model Building**: It creates decision variables and translates all scheduling rules into mathematical constraints.
3. **Iterative Solving**: The CP-SAT solver attempts to find a solution, starting with high staffing requirements and automatically reducing them until a feasible schedule is found.

The system treats all submitted leave requests as pre-approved during the scheduling process and automatically identifies the highest achievable minimum staffing level given the leave requests. This provides a clear picture of the "worst-case" staffing scenario if all requested leave were granted. Final leave approval remains at the discretion of department chiefs.

### Cross-Department Scheduling 

Some primary care providers at Chinle serve in multiple departments, for example, working clinic in Family Practice while also taking call or clinic shifts in Pediatrics. To manage this complexity, the scheduler is run sequentially by department in a way that accounts for interdependencies:

* **Pediatrics First**: The Pediatrics schedule is generated first. This determines call and clinic assignments for all providers involved in Pediatric coverage, including those who also work in other departments.
* **Internal Medicine and Family Practice Next**: Once the Pediatrics schedule is finalized, it is used to block off corresponding dates for cross-department providers. These constraints are then incorporated into the Internal Medicine and Family Practice scheduling runs to ensure providers aren’t double-booked or overcommitted.

This staged approach ensures that cross-department providers are scheduled consistently and without conflict across the three services. It also reflects the higher coordination demands of Pediatrics, where call coverage is tightly structured and less flexible than general clinic staffing.

## Scheduler Output

The scheduler produces a binary output for each provider, day, and session:

- 1 = scheduled to work in clinic or call
- 0 = not scheduled (ie., admin, RDO, inpatient, or leave) 

This binary decision matrix is then translated into user-friendly outputs which provide detailed information about the quality of the scheduling solution.

### Calendar 

An HTML calendar is generated for each department using the scheudle dataframe. 

### Schedule Dataframe

The primary dataframe output is the complete schedule showing which providers are assigned to each session:

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

## Quality Assurance 

All schedules generated by the scheduler serve as first drafts and are thoroughly reviewed by department chiefs and Alberta Begay before implementation. 

## Usage

A walkthrough of how the Internal Medicine, Family Practice, and Pediatric schedules were created for August 2025 can be found in `notebooks/august_schedule.ipynb`. The August 2025 clinic and call schedule for all three departments was generated in under 3 seconds, staffing 160+ clinic sessions while respecting 45+ leave requests and inpatient requirements.

## Directory Architecture

```bash
├── config/                        # Clinic and provider rules 
│   ├── family_practice.yml
│   ├── internal_medicine.yml
│   └── pediatrics.yml
├── constraints/                   # Scheduling rules and constraint logic
│   ├── family_practice.py
│   ├── internal_medicine.py
│   └── pediatrics.py
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
├── output/                        # Schedule output for August by department saved as CSV and HTML files
│   └── august/
│       ├── family_practice/
│       │   ├── calendar.html
│       │   ├── provider_summary_df.csv
│       │   └── schedule_df.csv
│       ├── internal_medicine/
│       │   ├── calendar.html
│       │   ├── provider_summary_df.csv
│       │   └── schedule_df.csv
│       └── pediatrics/
│           ├── calendar.html
│           ├── call_summary_df.csv 
│           ├── provider_summary_df.csv
│           └── schedule_df.csv
└── utils/                         # Input parsing and calendar creation
    ├── calendar.py
    ├── calendar_formatter.py
    └── parser.py
```

## Requirements

Built and tested in python 3.13.

Core dependencies: 
- pandas
- numpy
- pyyaml
- ortools
