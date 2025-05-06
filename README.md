# Chinle Primary Care Scheduler 
This project is a custom-built Python tool to help automate and streamline the creation of outpatient clinic schedules for internal medicine, family practice, and pediatrics. It accounts for each provider’s schedule preferences, clinic workload limits, time off, inpatient assignments, and federal holidays.

The goal is to make the scheduling process more transparent, consistent, and timely. Ideally, this tool will allow the entire outpatient schedule to be generated shortly after the inpatient schedule and leave requests are finalized.

Currently, only the internal medicine scheduler is active. Versions for family practice and pediatrics are in development.

## Problem Overview
Creating fair and functional clinic schedules is complex. This tool treats the task as a constraint-based puzzle, searching for a schedule that satisfies all pre-defined rules.

While the number of possible schedules is enormous, especially across months and departments, only a small fraction are valid. To solve this efficiently, we use CP-SAT, a powerful open-source solver developed by Google that’s designed for precisely this kind of constraint satisfaction problem.

## Inputs 
The scheduler relies on three main inputs:

1. Calendar – the date range over which the schedule will be built (see `utils/calendar.py`)

2. Data – inpatient assignments and leave requests (see `data/inpatient.csv` and `data/leave_request.csv`)

3. Rules – codified constraints that define how providers can be scheduled

General clinic and provider-specific rules are defined in `config/internal_medicine.yml` file. These include clinic days, maximum clinics per week, RDO preferences, staffing requirements, and more. The rules are imported and codified in `constraints/internal_medicine.py`. For a plain-English summary of the logic, see `docs/internal_medicine_rules.pdf`. 

## Output

With these inputs, The CP-SAT model produces a binary output for each provider, day, and session:

- 1 = scheduled to work in clinic
- 0 = not scheduled (admin, RDO, inpatient, or leave) 

Clinic staffing can be determined by identifying which providers have a value of 1 for each day and session.

```yaml
Solution found in 0.000013 seconds
2025-08-04 Monday morning: staffed by ['Mccrae', 'Miles', 'Selig']
2025-08-04 Monday afternoon: staffed by ['Bornstein', 'Mccrae', 'Selig']
...
```

## Usage

A full walkthrough of how the internal medicine schedule was created for August 2025 can be found in `tutorial/tutorial.md`.

## Directory Architecture

```bash
├── engine/                     # Entry point for running the scheduler
│   └── engine.py
├── constraints/                # Scheduling rules and constraint logic
│   └── internal_medicine.py
├── utils/                      # Input parsing and calendar creation
│   ├── parser.py
│   └── calendar.py
├── config/                     # Clinic and provider rules 
│   └── internal_medicine.yml
└── data/                       # Input data
    ├── inpatient.csv
    └── leave_requests.csv 
```

## Requirements

Built and tested in python 3.13.

Core dependencies: 
- pandas
- pyyaml
- ortools
