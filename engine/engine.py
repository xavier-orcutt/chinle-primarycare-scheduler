import pandas as pd
from datetime import date
from collections import defaultdict
from utils.parser import parse_inputs
from utils.calendar import generate_clinic_calendar
from constraints.internal_medicine import (
    create_shift_variables,
    add_leave_constraints,
    add_rdo_constraints,
    add_clinic_count_constraints,
    add_min_max_staffing_constraints,
    add_inpatient_block_constraints
)
from ortools.sat.python import cp_model

# Parse all inputs (YAML, CSVs)
config, leave_df, inpatient_days_df, inpatient_starts_df = parse_inputs('config/internal_medicine.yml',
                                                                        'data/leave_requests.csv',
                                                                        'data/inpatient.csv')

# Build calendar
calendar = generate_clinic_calendar(date(2025, 8, 4), date(2025, 8, 29), config['clinic_rules'])

# Create model and shift variables 
model = cp_model.CpModel()
shift_vars = create_shift_variables(model, list(config['providers'].keys()), calendar)

# Add constraints 
objective_terms = []

add_leave_constraints(model, shift_vars, leave_df)
add_inpatient_block_constraints(model, shift_vars, inpatient_starts_df, inpatient_days_df)
add_clinic_count_constraints(model, shift_vars, config['providers'], inpatient_starts_df)
add_rdo_constraints(model, shift_vars, leave_df, inpatient_days_df, config['clinic_rules'], config['providers'])
add_min_max_staffing_constraints(model, shift_vars, calendar, config['clinic_rules'])

objective_terms.extend(add_clinic_count_constraints(model, shift_vars, config['providers'], inpatient_starts_df))

if objective_terms:
    model.Minimize(sum(objective_terms))

# Solve model
solver = cp_model.CpSolver()
solver.parameters.random_seed = 42

status = solver.Solve(model)

# Output shift variable values
clinic_rules = config['clinic_rules']

if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
    print(f"Solution found") 
    
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

