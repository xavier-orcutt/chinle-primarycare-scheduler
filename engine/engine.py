import pandas as pd
from datetime import date
from utils.parser import parse_inputs
from utils.calendar import generate_clinic_calendar
from constraints.internal_medicine import (
    create_shift_variables,
    add_inpatient_rdo_constraints,
    add_inpatient_block_constraints,
)
from ortools.sat.python import cp_model

# Parse all inputs (YAML, CSVs)
config, leave_df, inpatient_days_df, inpatient_starts_df = parse_inputs('config/internal_medicine.yml',
                                                                        'data/leave_requests.csv',
                                                                        'data/inpatient.csv')

# Build calendar
calendar = generate_clinic_calendar(date(2025, 6, 2), date(2025, 6, 6), config['clinic_rules'])

# Create model and shift variables 
model = cp_model.CpModel()
shift_vars = create_shift_variables(model, list(config['providers'].keys()), calendar)

# Add constraints
add_inpatient_rdo_constraints(model, shift_vars, inpatient_starts_df, config['clinic_rules'])
add_inpatient_block_constraints(model, shift_vars, inpatient_days_df)

# Solve model
solver = cp_model.CpSolver()
status = solver.Solve(model)

# Output shift variable values
print("Scheduled shifts:")
for provider in shift_vars:
    for day in calendar:
        for session in calendar[day]:
            var = shift_vars[provider][day][session]
            value = solver.Value(var)
            print(f"{provider}: {day} {session} → {value}")

if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
    print("Solution found.")
    # print output...
else:
    print("No feasible solution found.")

