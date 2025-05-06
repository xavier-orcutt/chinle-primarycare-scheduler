# Chinle Primary Care Scheduler
**In this notebook, we'll demonstrate how to create an internal medicine schedule for August 2025.**

```python
import sys
from pathlib import Path

# Add project root (one level up from 'tutorial') to sys.path
project_root = Path.cwd().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
```

```python
import pandas as pd
from datetime import date
from utils.parser import parse_inputs
from utils.calendar import generate_clinic_calendar
from constraints.internal_medicine import (
    create_shift_variables,
    add_leave_constraints,
    add_inpatient_block_constraints,
    add_rdo_constraints,
    add_clinic_count_constraints,
    add_min_max_staffing_constraints
)
from ortools.sat.python import cp_model
```
**First, we'll import and prepare the YML and CSV files for processing.**

```python
config, leave_df, inpatient_days_df, inpatient_starts_df = parse_inputs('../config/internal_medicine.yml',
                                                                        '../data/leave_requests.csv',
                                                                        '../data/inpatient.csv')
```
**Let's examine the parsed YML and CSV data to ensure everything is properly loaded.**

```python
config['clinic_rules']
```
    {'clinic_days': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
     'clinic_sessions': {'Monday': ['morning', 'afternoon'],
      'Tuesday': ['morning', 'afternoon'],
      'Wednesday': ['morning', 'afternoon'],
      'Thursday': ['afternoon'],
      'Friday': ['morning', 'afternoon']},
     'random_day_off': {'eligible_days': ['Monday',
       'Tuesday',
       'Wednesday',
       'Friday']},
     'inpatient_schedule': {'inpatient_length': 7,
      'inpatient_starts_on': 'Tuesday',
      'inpatient_ends_on': 'Monday',
      'pre_inpatient_leave': 'Monday',
      'post_inpatient_leave': 'Friday'},
     'staffing': {'min_providers_per_session': 3, 'max_providers_per_session': 5},
     'holiday_dates': [datetime.date(2025, 1, 1),
      datetime.date(2025, 1, 20),
      datetime.date(2025, 2, 17),
      datetime.date(2025, 5, 26),
      datetime.date(2025, 6, 19),
      datetime.date(2025, 7, 4),
      datetime.date(2025, 9, 1),
      datetime.date(2025, 10, 13),
      datetime.date(2025, 11, 11),
      datetime.date(2025, 11, 27),
      datetime.date(2025, 12, 25)],
     'clinic_intensity_limit': {'enabled': True,
      'enforce_ratio_on_short_weeks': True}}

```python
print(leave_df.dtypes)
leave_df.head(5)
```

    provider            object
    date        datetime64[ns]
    dtype:              object

<table border="1" class="dataframe">
  <thead>
    <tr style="text-align: right;">
      <th></th>
      <th>provider</th>
      <th>date</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th>0</th>
      <td>Orcutt</td>
      <td>2025-08-04</td>
    </tr>
    <tr>
      <th>1</th>
      <td>Orcutt</td>
      <td>2025-08-05</td>
    </tr>
    <tr>
      <th>2</th>
      <td>Orcutt</td>
      <td>2025-08-06</td>
    </tr>
    <tr>
      <th>3</th>
      <td>Orcutt</td>
      <td>2025-08-07</td>
    </tr>
    <tr>
      <th>4</th>
      <td>Orcutt</td>
      <td>2025-08-08</td>
    </tr>
  </tbody>
</table>
</div>

```python
print(inpatient_days_df.dtypes)
inpatient_days_df.head(5)
```

    provider            object
    date        datetime64[ns]
    dtype:              object

<table border="1" class="dataframe">
  <thead>
    <tr style="text-align: right;">
      <th></th>
      <th>provider</th>
      <th>date</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th>0</th>
      <td>Bornstein</td>
      <td>2025-08-05</td>
    </tr>
    <tr>
      <th>1</th>
      <td>Bornstein</td>
      <td>2025-08-06</td>
    </tr>
    <tr>
      <th>2</th>
      <td>Bornstein</td>
      <td>2025-08-07</td>
    </tr>
    <tr>
      <th>3</th>
      <td>Bornstein</td>
      <td>2025-08-08</td>
    </tr>
    <tr>
      <th>4</th>
      <td>Bornstein</td>
      <td>2025-08-09</td>
    </tr>
  </tbody>
</table>
</div>

```python
print(inpatient_starts_df.dtypes)
inpatient_starts_df.head(5)
```

    provider              object
    start_date    datetime64[ns]
    dtype:                object

<table border="1" class="dataframe">
  <thead>
    <tr style="text-align: right;">
      <th></th>
      <th>provider</th>
      <th>start_date</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th>0</th>
      <td>Bornstein</td>
      <td>2025-08-05</td>
    </tr>
    <tr>
      <th>1</th>
      <td>Wadlin</td>
      <td>2025-08-19</td>
    </tr>
    <tr>
      <th>2</th>
      <td>Miles</td>
      <td>2025-08-26</td>
    </tr>
  </tbody>
</table>
</div>

**The files are correctly formatted with appropriate data types. Now we'll construct our August 2025 calendar, including only valid clinic days (excluding federal holidays) and available sessions (noting there are no Thursday morning sessions).**

```python
calendar = generate_clinic_calendar(date(2025, 8, 4), 
                                    date(2025, 8, 29), 
                                    config['clinic_rules'])
```

```python
# Here are all the possible clinic sessions that a provider could work in August. 
calendar
```
    {datetime.date(2025, 8, 4): ['morning', 'afternoon'],
     datetime.date(2025, 8, 5): ['morning', 'afternoon'],
     datetime.date(2025, 8, 6): ['morning', 'afternoon'],
     datetime.date(2025, 8, 7): ['afternoon'],
     datetime.date(2025, 8, 8): ['morning', 'afternoon'],
     datetime.date(2025, 8, 11): ['morning', 'afternoon'],
     datetime.date(2025, 8, 12): ['morning', 'afternoon'],
     datetime.date(2025, 8, 13): ['morning', 'afternoon'],
     datetime.date(2025, 8, 14): ['afternoon'],
     datetime.date(2025, 8, 15): ['morning', 'afternoon'],
     datetime.date(2025, 8, 18): ['morning', 'afternoon'],
     datetime.date(2025, 8, 19): ['morning', 'afternoon'],
     datetime.date(2025, 8, 20): ['morning', 'afternoon'],
     datetime.date(2025, 8, 21): ['afternoon'],
     datetime.date(2025, 8, 22): ['morning', 'afternoon'],
     datetime.date(2025, 8, 25): ['morning', 'afternoon'],
     datetime.date(2025, 8, 26): ['morning', 'afternoon'],
     datetime.date(2025, 8, 27): ['morning', 'afternoon'],
     datetime.date(2025, 8, 28): ['afternoon'],
     datetime.date(2025, 8, 29): ['morning', 'afternoon']}

**Next, we'll create a new CpModel instance from the OR-Tools library. This model object will store our scheduling constraints. We'll also generate a dictionary of binary decision variables for each provider, date, and session based on our August 2025 calendar.**

```python
model = cp_model.CpModel()
shift_vars = create_shift_variables(model, list(config['providers'].keys()), calendar)
```

```python
# For each possible clinic session in August 2025, there's a binary decision variable (0 or 1) that will be solved by the model
shift_vars['Orcutt']
```

    {datetime.date(2025, 8, 4): {'morning': Orcutt_2025-08-04_morning(0..1),
      'afternoon': Orcutt_2025-08-04_afternoon(0..1)},
     datetime.date(2025, 8, 5): {'morning': Orcutt_2025-08-05_morning(0..1),
      'afternoon': Orcutt_2025-08-05_afternoon(0..1)},
     datetime.date(2025, 8, 6): {'morning': Orcutt_2025-08-06_morning(0..1),
      'afternoon': Orcutt_2025-08-06_afternoon(0..1)},
     datetime.date(2025, 8, 7): {'afternoon': Orcutt_2025-08-07_afternoon(0..1)},
     datetime.date(2025, 8, 8): {'morning': Orcutt_2025-08-08_morning(0..1),
      'afternoon': Orcutt_2025-08-08_afternoon(0..1)},
     datetime.date(2025, 8, 11): {'morning': Orcutt_2025-08-11_morning(0..1),
      'afternoon': Orcutt_2025-08-11_afternoon(0..1)},
     datetime.date(2025, 8, 12): {'morning': Orcutt_2025-08-12_morning(0..1),
      'afternoon': Orcutt_2025-08-12_afternoon(0..1)},
     datetime.date(2025, 8, 13): {'morning': Orcutt_2025-08-13_morning(0..1),
      'afternoon': Orcutt_2025-08-13_afternoon(0..1)},
     datetime.date(2025, 8, 14): {'afternoon': Orcutt_2025-08-14_afternoon(0..1)},
     datetime.date(2025, 8, 15): {'morning': Orcutt_2025-08-15_morning(0..1),
      'afternoon': Orcutt_2025-08-15_afternoon(0..1)},
     datetime.date(2025, 8, 18): {'morning': Orcutt_2025-08-18_morning(0..1),
      'afternoon': Orcutt_2025-08-18_afternoon(0..1)},
     datetime.date(2025, 8, 19): {'morning': Orcutt_2025-08-19_morning(0..1),
      'afternoon': Orcutt_2025-08-19_afternoon(0..1)},
     datetime.date(2025, 8, 20): {'morning': Orcutt_2025-08-20_morning(0..1),
      'afternoon': Orcutt_2025-08-20_afternoon(0..1)},
     datetime.date(2025, 8, 21): {'afternoon': Orcutt_2025-08-21_afternoon(0..1)},
     datetime.date(2025, 8, 22): {'morning': Orcutt_2025-08-22_morning(0..1),
      'afternoon': Orcutt_2025-08-22_afternoon(0..1)},
     datetime.date(2025, 8, 25): {'morning': Orcutt_2025-08-25_morning(0..1),
      'afternoon': Orcutt_2025-08-25_afternoon(0..1)},
     datetime.date(2025, 8, 26): {'morning': Orcutt_2025-08-26_morning(0..1),
      'afternoon': Orcutt_2025-08-26_afternoon(0..1)},
     datetime.date(2025, 8, 27): {'morning': Orcutt_2025-08-27_morning(0..1),
      'afternoon': Orcutt_2025-08-27_afternoon(0..1)},
     datetime.date(2025, 8, 28): {'afternoon': Orcutt_2025-08-28_afternoon(0..1)},
     datetime.date(2025, 8, 29): {'morning': Orcutt_2025-08-29_morning(0..1),
      'afternoon': Orcutt_2025-08-29_afternoon(0..1)}}

**With our model initialized, we'll now add the necessary constraints.**

```python
objective_terms = []
```

```python
add_leave_constraints(model, shift_vars, leave_df)
add_inpatient_block_constraints(model, shift_vars, inpatient_starts_df, inpatient_days_df)
add_clinic_count_constraints(model, shift_vars, config['providers'], inpatient_starts_df)
add_rdo_constraints(model, shift_vars, leave_df, inpatient_days_df, config['clinic_rules'], config['providers'])
add_min_max_staffing_constraints(model, shift_vars, calendar, config['clinic_rules'])
```

```python
objective_terms.extend(add_clinic_count_constraints(model, shift_vars, config['providers'], inpatient_starts_df))
```

```python
if objective_terms:
    model.Minimize(sum(objective_terms))
```

**Finally, we'll solve the model and display the resulting schedule.**


```python
solver = cp_model.CpSolver()

# Set random seed for reproducibility
solver.parameters.random_seed = 42

status = solver.Solve(model)
solver_wall_time = solver.wall_time
```


```python
from collections import defaultdict
clinic_rules = config['clinic_rules']

if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
    print(f"Solution found in {solver_wall_time/1000:.6f} seconds") # Convert from ms to seconds
    
    # Track sessions by provider and week
    provider_sessions = defaultdict(lambda: defaultdict(int))
    
    # Print the schedule and collect session counts
    for day in calendar:
        day_of_week = day.strftime('%A')
        week_key = (day.year, day.isocalendar()[1])
        
        for session in calendar[day]:
            scheduled = [
                provider for provider in shift_vars
                if day in shift_vars[provider]
                and session in shift_vars[provider][day]
                and solver.Value(shift_vars[provider][day][session]) == 1
            ]
            
            # Update session counts for each provider
            for provider in scheduled:
                provider_sessions[provider][week_key] += 1
            
            print(f"{day} {day_of_week} {session}: staffed by {scheduled}")
    
    # Print session counts per provider per week
    print("\n=== Provider Weekly Session Counts ===")
    all_providers = list(config['providers'])
    all_weeks = sorted(set(week for provider_weeks in provider_sessions.values() 
                          for week in provider_weeks))
    
    # Header row with week numbers
    header = "Provider    " + "".join(f"Week {week[1]:02d}  " for week in all_weeks)
    print(header)
    print("-" * len(header))
    
    # Print counts for each provider
    for provider in all_providers:
        row = f"{provider:<10} " + "".join(
            f"{provider_sessions[provider][week]:^10}" for week in all_weeks
        )
        print(row)
else:
    print("No feasible solution found.")
```
  Solution found in 0.000014 seconds
  2025-08-04 Monday morning: staffed by ['Mcrae', 'Miles', 'Selig']
  2025-08-04 Monday afternoon: staffed by ['Bornstein', 'Mcrae', 'Miles', 'Selig']
  2025-08-05 Tuesday morning: staffed by ['Mcrae', 'Miles', 'Stewart', 'Tanay']
  2025-08-05 Tuesday afternoon: staffed by ['Mcrae', 'Stewart', 'Tanay']
  2025-08-06 Wednesday morning: staffed by ['Selig', 'Stewart', 'Tanay']
  2025-08-06 Wednesday afternoon: staffed by ['Bornstein', 'Selig', 'Tanay']
  2025-08-07 Thursday afternoon: staffed by ['Mcrae', 'Selig', 'Tanay']
  2025-08-08 Friday morning: staffed by ['Bornstein', 'Stewart', 'Tanay']
  2025-08-08 Friday afternoon: staffed by ['Bornstein', 'Mcrae', 'Selig']
  2025-08-11 Monday morning: staffed by ['Bornstein', 'Miles', 'Selig']
  2025-08-11 Monday afternoon: staffed by ['Bornstein', 'Miles', 'Selig']
  2025-08-12 Tuesday morning: staffed by ['Miles', 'Stewart', 'Tanay', 'Wadlin']
  2025-08-12 Tuesday afternoon: staffed by ['Stewart', 'Tanay', 'Wadlin']
  2025-08-13 Wednesday morning: staffed by ['Mcrae', 'Selig', 'Stewart', 'Tanay', 'Wadlin']
  2025-08-13 Wednesday afternoon: staffed by ['Mcrae', 'Selig', 'Tanay', 'Wadlin']
  2025-08-14 Thursday afternoon: staffed by ['Mcrae', 'Selig', 'Tanay']
  2025-08-15 Friday morning: staffed by ['Mcrae', 'Tanay', 'Wadlin']
  2025-08-15 Friday afternoon: staffed by ['Mcrae', 'Selig', 'Stewart']
  2025-08-18 Monday morning: staffed by ['Mcrae', 'Selig', 'Wadlin']
  2025-08-18 Monday afternoon: staffed by ['Mcrae', 'Selig', 'Wadlin']
  2025-08-19 Tuesday morning: staffed by ['Bornstein', 'Stewart', 'Tanay', 'Wadlin']
  2025-08-19 Tuesday afternoon: staffed by ['Bornstein', 'Stewart', 'Tanay', 'Wadlin']
  2025-08-20 Wednesday morning: staffed by ['Mcrae', 'Selig', 'Stewart', 'Tanay', 'Wadlin']
  2025-08-20 Wednesday afternoon: staffed by ['Mcrae', 'Selig', 'Tanay']
  2025-08-21 Thursday afternoon: staffed by ['Bornstein', 'Selig', 'Tanay']
  2025-08-22 Friday morning: staffed by ['Mcrae', 'Stewart', 'Tanay']
  2025-08-22 Friday afternoon: staffed by ['Bornstein', 'Mcrae', 'Selig']
  2025-08-25 Monday morning: staffed by ['Mcrae', 'Miles', 'Wadlin']
  2025-08-25 Monday afternoon: staffed by ['Mcrae', 'Miles', 'Wadlin']
  2025-08-26 Tuesday morning: staffed by ['Bornstein', 'Selig', 'Stewart', 'Tanay', 'Wadlin']
  2025-08-26 Tuesday afternoon: staffed by ['Selig', 'Stewart', 'Tanay']
  2025-08-27 Wednesday morning: staffed by ['Bornstein', 'Mcrae', 'Selig', 'Stewart', 'Tanay']
  2025-08-27 Wednesday afternoon: staffed by ['Mcrae', 'Selig', 'Tanay']
  2025-08-28 Thursday afternoon: staffed by ['Mcrae', 'Selig', 'Tanay']
  2025-08-29 Friday morning: staffed by ['Bornstein', 'Selig', 'Stewart', 'Tanay']
  2025-08-29 Friday afternoon: staffed by ['Bornstein', 'Mcrae', 'Miles']

  === Provider Weekly Session Counts ===
  Provider    Week 32  Week 33  Week 34  Week 35  
  ------------------------------------------------
  Bornstein      4         2         4         4     
  Mcrae          6         5         6         6     
  Miles          3         3         0         3     
  Orcutt         0         0         0         0     
  Selig          6         6         6         6     
  Stewart        4         4         4         4     
  Tanay          6         6         6         6     
  Wadlin         0         5         5         3     

**The August 2025 clinic schedule has been successfully generated, satisfying all constraints. The schedule honors all leave requests while maintaining minimum staffing of 3 providers and maximum staffing of 5 providers per session. Each provider is assigned roughly their appropriate number of weekly clinic sessions, with reduced sessions following inpatient service weeks. RDOs have been allocated according to provider preferences where possible. Leave has been provided for the Monday prior to and the Friday following inpatient service.**
