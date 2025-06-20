import logging
import copy
import pandas as pd
import numpy as np
from collections import defaultdict
from ortools.sat.python import cp_model
from datetime import timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def calculate_consecutive_clinics(provider, week_dates, shift_vars, solver, calendar):
    """
    Calculate the maximum consecutive clinic sessions for a provider in a given week.
    Thursday morning admin time breaks consecutive streaks (ie., we reset the count at Thursday PM). 
    
    Only counts morning and afternoon clinic sessions where the provider is actually scheduled.
    Does NOT count call sessions.
    """
    max_consecutive = 0
    current_consecutive = 0
    
    for date in sorted(week_dates):
        if date in calendar:
            # Process morning session if it exists in calendar
            if 'morning' in calendar[date]:
                is_scheduled = (
                    date in shift_vars[provider] and 
                    'morning' in shift_vars[provider][date] and
                    solver.Value(shift_vars[provider][date]['morning']) == 1
                )
                
                if is_scheduled:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 0
            
            # Reset consecutive count at Thursday afternoon (due to Thursday AM admin break)
            if date.strftime('%A') == 'Thursday' and 'afternoon' in calendar[date]:
                current_consecutive = 0  # Reset because of Thursday AM admin break
            
            # Process afternoon session if it exists in calendar
            if 'afternoon' in calendar[date]:
                is_scheduled = (
                    date in shift_vars[provider] and 
                    'afternoon' in shift_vars[provider][date] and
                    solver.Value(shift_vars[provider][date]['afternoon']) == 1
                )
                
                if is_scheduled:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 0
    
    return max_consecutive

def create_enhanced_provider_summary(shift_vars, solver, config, calendar):
    """
    Creates enhanced provider summary with shortened weeks (ie., Monday or Friday off), 
    AM/PM split, and consecutive clinic tracking.
    
    Parameters:
    ----------
    shift_vars : dict
        Nested dict of binary decision variables
    solver : cp_model.CpSolver
        Solved OR-Tools solver instance
    config : dict
        Configuration dictionary with provider information
    calendar : dict
        Calendar dictionary with date keys and session values
        
    Returns:
    -------
    pd.DataFrame
        Enhanced provider summary DataFrame
    """
    # Weekly tracking with consecutive
    provider_weekly_data = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'consecutive': 0}))
    
    # Total AM/PM tracking across all dates
    provider_totals = defaultdict(lambda: {'total_AM': 0, 'total_PM': 0})

    # Monday/Friday off tracking
    provider_monday_friday_off = defaultdict(int)
    
    all_providers = list(config['providers'].keys())
    
    # Group dates by week
    dates_by_week = defaultdict(list)
    
    for day in sorted(calendar.keys()):
        week_key = (day.year, day.isocalendar()[1])
        dates_by_week[week_key].append(day)

    # Filter out weeks that are just a single Sunday (pediatrics edge case)
    filtered_dates_by_week = {}
    for week_key, week_dates in dates_by_week.items():
        # Keep week if it has more than 1 day, or if the single day isn't Sunday
        if len(week_dates) > 1 or (len(week_dates) == 1 and week_dates[0].strftime('%A') != 'Sunday'):
            filtered_dates_by_week[week_key] = week_dates

    dates_by_week = filtered_dates_by_week

    # Process each week for total sessions and consecutive tracking
    for week_key, week_dates in dates_by_week.items():
        for provider in all_providers:
            total_sessions = 0

            # Track Monday/Friday sessions for this provider this week
            monday_sessions = 0
            friday_sessions = 0
            
            # Count total clinic sessions for the week
            for day in week_dates:
                day_of_week = day.strftime('%A')

                for session in calendar.get(day, []):
                    if session in ['morning', 'afternoon']:
                        if (day in shift_vars[provider] and 
                            session in shift_vars[provider][day] and
                            solver.Value(shift_vars[provider][day][session]) == 1):
                            total_sessions += 1

                            # Count Monday/Friday sessions
                            if day_of_week == 'Monday':
                                monday_sessions += 1
                            elif day_of_week == 'Friday':
                                friday_sessions += 1
            
            # Calculate consecutive sessions for the week
            consecutive = calculate_consecutive_clinics(provider, 
                                                        week_dates, 
                                                        shift_vars, 
                                                        solver, 
                                                        calendar)
            
            provider_weekly_data[provider][week_key] = {
                'total': total_sessions,
                'consecutive': consecutive
            }

            # Check if provider is off Monday or Friday
            if monday_sessions == 0 or friday_sessions == 0:
                provider_monday_friday_off[provider] += 1
    
    # Process all dates for total AM/PM tracking
    for provider in all_providers:
        total_am = 0
        total_pm = 0
        
        for day in calendar.keys():  # All days in the calendar
            for session in calendar[day]:
                if (day in shift_vars[provider] and 
                    session in shift_vars[provider][day] and
                    solver.Value(shift_vars[provider][day][session]) == 1):
                    
                    if session == 'morning':
                        total_am += 1
                    elif session == 'afternoon':
                        total_pm += 1
        
        # Store total AM/PM for this provider
        provider_totals[provider] = {
            'total_AM': total_am,
            'total_PM': total_pm
        }
    
    # Build the summary DataFrame
    all_weeks = sorted(dates_by_week.keys())
    
    summary_data = []
    for provider in all_providers:
        provider_data = {'provider': provider}
        total_sessions = 0
        
        # Add weekly columns with format "total, consecutive"
        for week in all_weeks:
            week_num = week[1]
            week_data = provider_weekly_data[provider][week]
            total = week_data['total']
            consecutive = week_data['consecutive']
            
            # Format as "total, consecutive"
            provider_data[f'week_{week_num}'] = f"{total}, {consecutive}"
            total_sessions += total
        
        # Add total sessions
        provider_data['total_sessions'] = total_sessions

        # Add Monday/Friday off count
        provider_data['monday_or_friday_off'] = provider_monday_friday_off[provider]
        
        # Add total AM/PM columns
        provider_data['total_AM'] = provider_totals[provider]['total_AM']
        provider_data['total_PM'] = provider_totals[provider]['total_PM']
        
        summary_data.append(provider_data)
    
    return pd.DataFrame(summary_data)

def create_im_schedule(
        config_path,
        leave_requests_path,
        inpatient_path,
        start_date,
        end_date,
        min_staffing_search = True,
        initial_min_providers = 4,
        random_seed = 42):
    """
    Creates a schedule for the internal medicine department.
    
    Parameters:
    ----------
    config_path : str
        Path to internal medicine YAML config file

    leave_requests_path : str
        Path to the leave requests CSV file

    inpatient_path : str
        Path to the inpatient assignments CSV file

    start_date : date
        Start date for the schedule. Must be a datetime.date object, e.g., date(2025, 3, 31), no leading zeros. 

    end_date : date
        End date for the schedule. Must be a datetime.date object, e.g., date(2025, 3, 31), no leading zeros. 

    min_staffing_search : bool
        If True, iteratively searches for highest feasible min_providers_per_session value
        
    initial_min_providers : int
        Starting value for min_providers_per_session when min_staffing_search is True

    random_seed : int
        Random seed for the solver
        
    Returns:
    -------
    tuple
        (schedule_df, provider_summary_df, solution_status)
    """
    logger.info(f"Creating schedule for Internal Medicine from {start_date} to {end_date}")
    
    # Import internal medicine specific modules
    from utils.parser import parse_inputs
    from utils.calendar import generate_clinic_calendar
    from constraints.internal_medicine import (
        create_shift_variables,
        add_leave_constraints,
        add_inpatient_block_constraints,
        add_clinic_count_constraints,
        add_rdo_constraints,
        add_min_max_staffing_constraints
    )

    # Parse all inputs (YAML, CSVs)
    logger.info("Parsing input files")
    config, leave_df, inpatient_days_df, inpatient_starts_df = parse_inputs(config_path, 
                                                                            leave_requests_path, 
                                                                            inpatient_path)
    
    # Build calendar
    logger.info("Building calendar")
    calendar = generate_clinic_calendar(start_date, 
                                        end_date, 
                                        config['clinic_rules'])

    # Store original config for reference
    original_config = copy.deepcopy(config)
    
    # Initialize solution tracking variables
    best_schedule_df = None
    best_provider_summary_df = None
    best_solution_status = None
    total_solve_time = 0
    
    # If min_staffing_search is enabled, iteratively try different min_providers values
    if min_staffing_search:
        min_provider_values = list(range(initial_min_providers, -1, -1))
        logger.info(f"Beginning iterative min_providers search, starting with {initial_min_providers}")
    else:
        # Just use the value from the YAML file
        min_provider_values = [config['clinic_rules'].get('staffing', {}).get('min_providers_per_session', 3)]
        logger.info(f"Using min_providers={min_provider_values[0]} from config")

   # Try each min_providers value until we find a feasible solution
    for min_providers in min_provider_values:
        # Create a copy of the config and update the min_providers value
        current_config = copy.deepcopy(original_config)

        # Update the min_providers value in the config copy
        if 'staffing' not in current_config['clinic_rules']:
            current_config['clinic_rules']['staffing'] = {}
        
        current_config['clinic_rules']['staffing']['min_providers_per_session'] = min_providers
                
        logger.info(f"Attempting to solve with min_providers_per_session = {min_providers}")

        # Create model and shift variables 
        model = cp_model.CpModel()
        shift_vars = create_shift_variables(model, 
                                            list(current_config['providers'].keys()), 
                                            calendar)
        
        # Initialize objective terms 
        objective_terms = []
        
        # Add leave and inpatient blocking constraints 
        add_leave_constraints(model, 
                              shift_vars, 
                              leave_df)
        
        add_inpatient_block_constraints(model, 
                                        shift_vars, 
                                        inpatient_starts_df, 
                                        inpatient_days_df)
        
        # Add clinic workload constraints
        clinic_objective_terms = add_clinic_count_constraints(model, 
                                                              shift_vars, 
                                                              current_config['providers'], 
                                                              inpatient_starts_df)
        objective_terms.extend(clinic_objective_terms)
        
        # Adding random day off (RDO) constraints
        add_rdo_constraints(model, 
                            shift_vars, 
                            leave_df, 
                            inpatient_days_df, 
                            current_config['clinic_rules'], 
                            current_config['providers'])
        
        # Add global clinic max/min staffing constraints
        add_min_max_staffing_constraints(model, 
                                         shift_vars, 
                                         calendar, 
                                         current_config['clinic_rules'])
        
        # Set objective if there are terms to minimize
        if objective_terms:
            model.Minimize(sum(objective_terms))
        
        # Solve model
        solver = cp_model.CpSolver()
        solver.parameters.random_seed = random_seed
        solver.parameters.max_time_in_seconds = 300  # 5-minute time limit
        
        status = solver.Solve(model)
        solver_wall_time = solver.wall_time
        total_solve_time += solver_wall_time

        # Process results if solution found
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            logger.info(f"Found {'optimal' if status == cp_model.OPTIMAL else 'feasible'} solution with min_providers = {min_providers}")
            
            # Create a DataFrame to store the schedule
            schedule_data = []
            
            for day in sorted(calendar.keys()):
                day_of_week = day.strftime('%A')
        
                for session in calendar[day]:
                    scheduled = [
                        provider for provider in shift_vars
                        if day in shift_vars[provider]
                        and session in shift_vars[provider][day]
                        and solver.Value(shift_vars[provider][day][session]) == 1
                    ]
                    
                    # Add to schedule data
                    schedule_data.append({
                        'date': day,
                        'day_of_week': day_of_week,
                        'session': session,
                        'providers': ','.join(scheduled),
                        'count': len(scheduled)
                    })
            
            # Convert to DataFrame
            schedule_df = pd.DataFrame(schedule_data)
            
            # Create enhanced provider summary DataFrame
            provider_summary_df = create_enhanced_provider_summary(shift_vars, 
                                                                   solver, 
                                                                   current_config, 
                                                                   calendar)
            
            # Create solution status dictionary
            solution_status = {
                'Status': 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE',
                'Minimum providers per session': min_providers,
                'Objective value': solver.ObjectiveValue() if objective_terms else None,
                'Solve time': f'{total_solve_time:3f} seconds',
                'Branches': solver.NumBranches(),
                'Conflicts': solver.NumConflicts()
            }
            
            # Store best solution
            best_schedule_df = schedule_df
            best_provider_summary_df = provider_summary_df
            best_solution_status = solution_status
            
            # If we're searching for min_providers and found a solution, we're done
            # (since we start with highest value and work downward)
            if min_staffing_search:
                logger.info(f"Successfully found solution with highest possible min_providers = {min_providers}")
                break
        else:
            logger.warning(f"No feasible solution found with min_providers = {min_providers}")
            
            # If we're not doing a search (using fixed value from config), return immediately
            if not min_staffing_search:
                solution_status = {
                    'status': 'infeasible' if status == cp_model.INFEASIBLE else 
                             ('model_invalid' if status == cp_model.MODEL_INVALID else
                              ('unknown' if status == cp_model.UNKNOWN else 'error')),
                    'min_providers_per_session': min_providers,
                    'is_optimal': False,
                    'solve_time': solver_wall_time,
                    'total_solve_time': total_solve_time,
                    'branches': solver.NumBranches(),
                    'conflicts': solver.NumConflicts()
                }
                return None, None, solution_status
    
    # Check if any solution was found
    if best_schedule_df is None:
        logger.error("No feasible solution found with any min_providers value")
        solution_status = {
            'status': 'infeasible',
            'min_providers_tried': min_provider_values,
            'is_optimal': False,
            'total_solve_time': total_solve_time
        }
        return None, None, solution_status
    
    return best_schedule_df, best_provider_summary_df, best_solution_status

def create_peds_schedule(
        config_path,
        leave_requests_path,
        inpatient_path,
        start_date,
        end_date,
        min_staffing_search = True,
        initial_min_providers = 4,
        random_seed = 42):
    """
    Creates a schedule for the pediatric department.
    
    Parameters:
    ----------
    config_path : str
        Path to pediatrics YAML config file

    leave_requests_path : str
        Path to the leave requests CSV file

    inpatient_path : str
        Path to the inpatient assignments CSV file

    start_date : date
        Start date for the schedule. Must be a datetime.date object, e.g., date(2025, 3, 31), no leading zeros. 

    end_date : date
        End date for the schedule. Must be a datetime.date object, e.g., date(2025, 3, 31), no leading zeros. 

    min_staffing_search : bool
        If True, iteratively searches for highest feasible min_providers_per_session value
        
    initial_min_providers : int
        Starting value for min_providers_per_session when min_staffing_search is True

    random_seed : int
        Random seed for the solver
        
    Returns:
    -------
    tuple
        (schedule_df, provider_summary_df, solution_status)
    """
    logger.info(f"Creating schedule for Pediatric from {start_date} to {end_date}")
    
    # Import pediatric specific modules
    from utils.parser import parse_inputs
    from utils.calendar import generate_pediatric_calendar
    from constraints.pediatrics import (
        create_shift_variables,
        add_leave_constraints,
        add_inpatient_block_constraints,
        add_call_constraints,
        add_monthly_call_limits,
        add_post_call_afternoon_constraints,
        add_clinic_count_constraints, 
        add_rdo_constraints,
        add_min_max_staffing_constraints
    )

    # Parse all inputs (YAML, CSVs)
    logger.info("Parsing input files")
    config, leave_df, inpatient_days_df, inpatient_starts_df = parse_inputs(config_path, 
                                                                            leave_requests_path, 
                                                                            inpatient_path)
    
    # Build calendar
    logger.info("Building calendar")
    calendar = generate_pediatric_calendar(start_date,
                                           end_date, 
                                           config['clinic_rules'])
    
    # Store original config for reference
    original_config = copy.deepcopy(config)
    
    # Initialize solution tracking variables
    best_schedule_df = None
    best_provider_summary_df = None
    best_call_summary_df = None
    best_solution_status = None
    total_solve_time = 0
    
    # If min_staffing_search is enabled, iteratively try different min_providers values
    if min_staffing_search:
        min_provider_values = list(range(initial_min_providers, -1, -1))
        logger.info(f"Beginning iterative min_providers search, starting with {initial_min_providers}")
    else:
        # Just use the value from the YAML file
        min_provider_values = [config['clinic_rules'].get('staffing', {}).get('min_providers_per_session', 3)]
        logger.info(f"Using min_providers={min_provider_values[0]} from config")

   # Try each min_providers value until we find a feasible solution
    for min_providers in min_provider_values:
        # Create a copy of the config and update the min_providers value
        current_config = copy.deepcopy(original_config)

        # Update the min_providers value in the config copy
        if 'staffing' not in current_config['clinic_rules']:
            current_config['clinic_rules']['staffing'] = {}
        
        current_config['clinic_rules']['staffing']['min_providers_per_session'] = min_providers
                
        logger.info(f"Attempting to solve with min_providers_per_session = {min_providers}")
        
        # Create model and shift variables 
        model = cp_model.CpModel()
        shift_vars = create_shift_variables(model, 
                                            list(current_config['providers'].keys()), 
                                            calendar)
        
        # Initialize objective terms 
        objective_terms = []
        
        # Add leave and inpatient blocking constraints 
        add_leave_constraints(model, 
                              shift_vars, 
                              leave_df)
        
        add_inpatient_block_constraints(model, 
                                        shift_vars, 
                                        inpatient_starts_df, 
                                        inpatient_days_df)
        
        # Add call constraint 
        call_objective_terms = add_call_constraints(model, 
                                                    shift_vars, 
                                                    leave_df, 
                                                    inpatient_starts_df, 
                                                    current_config['clinic_rules'],
                                                    current_config['providers'])
        
        objective_terms.extend(call_objective_terms)

        add_monthly_call_limits(model, 
                                shift_vars, 
                                calendar, 
                                current_config['providers'])
        
        add_post_call_afternoon_constraints(model, 
                                            shift_vars, 
                                            calendar)
        
        # Add clinic workload constraints
        clinic_objective_terms = add_clinic_count_constraints(model, 
                                                              shift_vars, 
                                                              current_config['providers'], 
                                                              inpatient_starts_df)
        
        objective_terms.extend(clinic_objective_terms)

        rdo_penalty_terms = add_rdo_constraints(model, 
                                                shift_vars, 
                                                leave_df, 
                                                inpatient_days_df, 
                                                current_config['clinic_rules'], 
                                                current_config['providers'])
        
        objective_terms.extend(rdo_penalty_terms) 
        
        # Add global clinic max/min staffing constraints
        add_min_max_staffing_constraints(model, 
                                         shift_vars, 
                                         calendar, 
                                         current_config['clinic_rules'])
        
        # Set objective if there are terms to minimize
        if objective_terms:
            model.Minimize(sum(objective_terms))
        
        # Solve model
        solver = cp_model.CpSolver()
        solver.parameters.random_seed = random_seed
        solver.parameters.max_time_in_seconds = 300  # 5-minute time limit
        
        status = solver.Solve(model)
        solver_wall_time = solver.wall_time
        total_solve_time += solver_wall_time

        # Process results if solution found
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            logger.info(f"Found {'optimal' if status == cp_model.OPTIMAL else 'feasible'} solution with min_providers = {min_providers}")
            
            # Create a DataFrame to store the schedule
            schedule_data = []
            call_schedule_data = []
            
            # Separate tracking for call sessions only
            call_sessions = defaultdict(lambda: defaultdict(int))
            
            for day in sorted(calendar.keys()):
                date_obj = day
                day_of_week = date_obj.strftime('%A')
                
                # For tracking weekly call, use Sunday-Thursday as a "call week"
                call_week_start = date_obj - timedelta(days=date_obj.weekday() + 1)  # Get Sunday
                call_week_key = (call_week_start.year, call_week_start.isocalendar()[1])
                
                for session in calendar[day]:
                    scheduled = [
                        provider for provider in shift_vars
                        if day in shift_vars[provider]
                        and session in shift_vars[provider][day]
                        and solver.Value(shift_vars[provider][day][session]) == 1
                    ]
                    
                    # Process call sessions separately for call summary
                    if session == 'call':
                        # Update call counts
                        for provider in scheduled:
                            call_sessions[provider][call_week_key] += 1
                        
                        # Add to call schedule data
                        call_schedule_data.append({
                            'date': day,
                            'day_of_week': day_of_week,
                            'provider': ','.join(scheduled)
                        })
                    
                    # Add to main schedule data (both clinic and call)
                    schedule_data.append({
                        'date': day,
                        'day_of_week': day_of_week,
                        'session': session,
                        'providers': ','.join(scheduled),
                        'count': len(scheduled)
                    })
            
            # Convert to DataFrames
            schedule_df = pd.DataFrame(schedule_data)
            schedule_df.loc[schedule_df['session'] == 'call', 'count'] = np.nan
            
            # Create enhanced provider summary (clinic sessions with AM/PM and consecutive)
            provider_summary_df = create_enhanced_provider_summary(
                shift_vars, solver, current_config, calendar
            )

            # Filter to only providers with max_clinics_per_week > 0
            providers_with_clinics = [
                provider for provider, config in current_config['providers'].items()
                if config.get('max_clinics_per_week', 0) > 0
            ]
            provider_summary_df = provider_summary_df[
                provider_summary_df['provider'].isin(providers_with_clinics)
            ].reset_index(drop=True)
            
            # Create call summary DataFrame (inline, call-specific logic)
            all_providers = list(current_config['providers'])
            call_weeks = sorted(set(week for provider_weeks in call_sessions.values()
                                for week in provider_weeks))
            
            call_summary_data = []
            for provider in all_providers:
                call_data = {'provider': provider}
                total_call = 0
                
                # Add call sessions by week
                for week in call_weeks:
                    week_num = week[1]
                    call_count = call_sessions[provider][week]
                    call_data[f'week_{week_num+1}'] = call_count
                    total_call += call_count
                
                # Add total call
                call_data['total_call'] = total_call
                call_summary_data.append(call_data)
                
            call_summary_df = pd.DataFrame(call_summary_data)
                    
            # Verify the actual minimum staffing level achieved for clinic sessions
            clinic_only_df = schedule_df[schedule_df['session'].isin(['morning', 'afternoon'])]
            min_staff_achieved = clinic_only_df['count'].min() if not clinic_only_df.empty else 0
            
            # Create a detailed status dictionary
            solution_status = {
                'Status': 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE',
                'Minimum providers per session': int(min_staff_achieved),
                'Objective value': solver.ObjectiveValue() if objective_terms else None,
                'Solve time': f'{total_solve_time:3f} seconds',
                'Branches': solver.NumBranches(),
                'Conflicts': solver.NumConflicts()
            }
            
            # Store best solution
            best_schedule_df = schedule_df
            best_provider_summary_df = provider_summary_df
            best_call_summary_df = call_summary_df
            best_solution_status = solution_status
            
            # If we're searching for min_providers and found a solution, we're done
            # (since we start with highest value and work downward)
            if min_staffing_search:
                logger.info(f"Successfully found solution with highest possible min_providers = {min_providers}")
                break
        else:
            logger.warning(f"No feasible solution found with min_providers = {min_providers}")
            
            # If we're not doing a search (using fixed value from config), return immediately
            if not min_staffing_search:
                solution_status = {
                    'status': 'infeasible' if status == cp_model.INFEASIBLE else 
                             ('model_invalid' if status == cp_model.MODEL_INVALID else
                              ('unknown' if status == cp_model.UNKNOWN else 'error')),
                    'min_providers_per_session': min_providers,
                    'is_optimal': False,
                    'solve_time': solver_wall_time,
                    'total_solve_time': total_solve_time,
                    'branches': solver.NumBranches(),
                    'conflicts': solver.NumConflicts()
                }
                return None, None, solution_status
    
    # Check if any solution was found
    if best_schedule_df is None:
        logger.error("No feasible solution found with any min_providers value")
        solution_status = {
            'status': 'infeasible',
            'min_providers_tried': min_provider_values,
            'is_optimal': False,
            'total_solve_time': total_solve_time
        }
        return None, None, None, solution_status
    
    return best_schedule_df, best_provider_summary_df, best_call_summary_df, best_solution_status

def create_fp_schedule(
        config_path,
        leave_requests_path,
        inpatient_path,
        peds_schedule_path,
        start_date,
        end_date,
        min_staffing_search = True,
        initial_min_providers = 4,
        random_seed = 42):
    """
    Creates a schedule for the family practice department.
    
    Parameters:
    ----------
    config_path : str
        Path to family practice YAML config file

    leave_requests_path : str
        Path to the leave requests CSV file

    inpatient_path : str
        Path to the inpatient assignments CSV file

    peds_schedule_path : str
        Path to the pediatric schedule CSV file for shared providers

    start_date : date
        Start date for the schedule. Must be a datetime.date object, e.g., date(2025, 3, 31), no leading zeros. 

    end_date : date
        End date for the schedule. Must be a datetime.date object, e.g., date(2025, 3, 31), no leading zeros. 

    min_staffing_search : bool
        If True, iteratively searches for highest feasible min_providers_per_session value
        
    initial_min_providers : int
        Starting value for min_providers_per_session when min_staffing_search is True

    random_seed : int
        Random seed for the solver
        
    Returns:
    -------
    tuple
        (schedule_df, provider_summary_df, solution_status)
    """
    logger.info(f"Creating schedule for Family Practice from {start_date} to {end_date}")
    
    # Import family practice specific modules
    from utils.parser import parse_inputs
    from utils.calendar import generate_clinic_calendar
    from constraints.family_practice import (
        create_shift_variables,
        add_leave_constraints,
        add_inpatient_block_constraints,
        add_pediatric_call_constraints,
        add_friday_only_constraints,
        add_clinic_count_constraints,
        add_fracture_clinic_constraints, 
        add_rdo_constraints,
        add_min_max_staffing_constraints
    )

    # Parse all inputs (YAML, CSVs)
    logger.info("Parsing input files")
    config, leave_df, inpatient_days_df, inpatient_starts_df = parse_inputs(config_path, 
                                                                            leave_requests_path, 
                                                                            inpatient_path)
    
    # Load pediatric schedule for shared providers (Powell and Shin)
    logger.info("Loading pediatric schedule")
    peds_schedule_df = None
    if peds_schedule_path:
        try:
            peds_schedule_df = pd.read_csv(peds_schedule_path)
            peds_schedule_df['date'] = pd.to_datetime(peds_schedule_df['date']).dt.date
            logger.info(f"Successfully loaded pediatric schedule with {len(peds_schedule_df)} rows")
            
        except Exception as e:
            logger.warning(f"Could not load pediatric schedule: {e}. Proceeding without it.")

    # Build calendar
    logger.info("Building calendar")
    calendar = generate_clinic_calendar(start_date, 
                                        end_date, 
                                        config['clinic_rules'])

    # Store original config for reference
    original_config = copy.deepcopy(config)
    
    # Initialize solution tracking variables
    best_schedule_df = None
    best_provider_summary_df = None
    best_solution_status = None
    total_solve_time = 0
    
    # If min_staffing_search is enabled, iteratively try different min_providers values
    if min_staffing_search:
        min_provider_values = list(range(initial_min_providers, -1, -1))
        logger.info(f"Beginning iterative min_providers search, starting with {initial_min_providers}")
    else:
        # Just use the value from the YAML file
        min_provider_values = [config['clinic_rules'].get('staffing', {}).get('min_providers_per_session', 3)]
        logger.info(f"Using min_providers={min_provider_values[0]} from config")

   # Try each min_providers value until we find a feasible solution
    for min_providers in min_provider_values:
        # Create a copy of the config and update the min_providers value
        current_config = copy.deepcopy(original_config)

        # Update the min_providers value in the config copy
        if 'staffing' not in current_config['clinic_rules']:
            current_config['clinic_rules']['staffing'] = {}
        
        current_config['clinic_rules']['staffing']['min_providers_per_session'] = min_providers
                
        logger.info(f"Attempting to solve with min_providers_per_session = {min_providers}")

        # Create model and shift variables 
        model = cp_model.CpModel()
        shift_vars = create_shift_variables(model, 
                                            list(current_config['providers'].keys()), 
                                            calendar)
        
        # Initialize objective terms 
        objective_terms = []
        
        # Add leave and inpatient blocking constraints 
        add_leave_constraints(model, 
                              shift_vars, 
                              leave_df)
        
        add_inpatient_block_constraints(model, 
                                        shift_vars, 
                                        inpatient_starts_df, 
                                        inpatient_days_df)
        
        # Add clinic workload constraints
        add_pediatric_call_constraints(model, 
                                       shift_vars, 
                                       peds_schedule_df)
        
        # Add Friday-only constraints 
        add_friday_only_constraints(model,
                                    shift_vars,
                                    calendar, 
                                    current_config['providers'])

        clinic_objective_terms = add_clinic_count_constraints(model, 
                                                              shift_vars, 
                                                              current_config['providers'], 
                                                              inpatient_starts_df,
                                                              peds_schedule_df)
        objective_terms.extend(clinic_objective_terms)

        # Add fracture clinic constraints (soft)
        fracture_clinic_objective_terms = add_fracture_clinic_constraints(model,
                                                                          shift_vars,
                                                                          calendar,
                                                                          current_config['providers'])
        
        objective_terms.extend(fracture_clinic_objective_terms)
        
        # Adding random day off (RDO) constraints
        add_rdo_constraints(model, 
                            shift_vars, 
                            leave_df, 
                            inpatient_days_df, 
                            current_config['clinic_rules'], 
                            current_config['providers'],
                            peds_schedule_df)
        
        # Add global clinic max/min staffing constraints
        add_min_max_staffing_constraints(model, 
                                         shift_vars, 
                                         calendar, 
                                         current_config['clinic_rules'])
        
        # Set objective if there are terms to minimize
        if objective_terms:
            model.Minimize(sum(objective_terms))
        
        # Solve model
        solver = cp_model.CpSolver()
        solver.parameters.random_seed = random_seed
        solver.parameters.max_time_in_seconds = 300  # 5-minute time limit
        
        status = solver.Solve(model)
        solver_wall_time = solver.wall_time
        total_solve_time += solver_wall_time

        # Process results if solution found
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            logger.info(f"Found {'optimal' if status == cp_model.OPTIMAL else 'feasible'} solution with min_providers = {min_providers}")
            
            # Create a DataFrame to store the schedule
            schedule_data = []
            
            for day in sorted(calendar.keys()):
                day_of_week = day.strftime('%A')
        
                for session in calendar[day]:
                    scheduled = [
                        provider for provider in shift_vars
                        if day in shift_vars[provider]
                        and session in shift_vars[provider][day]
                        and solver.Value(shift_vars[provider][day][session]) == 1
                    ]
                    
                    # Add to schedule data
                    schedule_data.append({
                        'date': day,
                        'day_of_week': day_of_week,
                        'session': session,
                        'providers': ','.join(scheduled),
                        'count': len(scheduled)
                    })
            
            # Convert to DataFrame
            schedule_df = pd.DataFrame(schedule_data)
            
            # Create enhanced provider summary DataFrame
            provider_summary_df = create_enhanced_provider_summary(shift_vars, 
                                                                   solver, 
                                                                   current_config, 
                                                                   calendar)
            
            # Create a detailed status dictionary
            solution_status = {
                'Status': 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE',
                'Minimum providers per session': min_providers,
                'Objective value': solver.ObjectiveValue() if objective_terms else None,
                'Solve time': f'{total_solve_time:3f} seconds',
                'Branches': solver.NumBranches(),
                'Conflicts': solver.NumConflicts()
            }
            
            # Store best solution
            best_schedule_df = schedule_df
            best_provider_summary_df = provider_summary_df
            best_solution_status = solution_status
            
            # If we're searching for min_providers and found a solution, we're done
            # (since we start with highest value and work downward)
            if min_staffing_search:
                logger.info(f"Successfully found solution with highest possible min_providers = {min_providers}")
                break
        else:
            logger.warning(f"No feasible solution found with min_providers = {min_providers}")
            
            # If we're not doing a search (using fixed value from config), return immediately
            if not min_staffing_search:
                solution_status = {
                    'status': 'infeasible' if status == cp_model.INFEASIBLE else 
                             ('model_invalid' if status == cp_model.MODEL_INVALID else
                              ('unknown' if status == cp_model.UNKNOWN else 'error')),
                    'min_providers_per_session': min_providers,
                    'is_optimal': False,
                    'solve_time': solver_wall_time,
                    'total_solve_time': total_solve_time,
                    'branches': solver.NumBranches(),
                    'conflicts': solver.NumConflicts()
                }
                return None, None, solution_status
    
    # Check if any solution was found
    if best_schedule_df is None:
        logger.error("No feasible solution found with any min_providers value")
        solution_status = {
            'status': 'infeasible',
            'min_providers_tried': min_provider_values,
            'is_optimal': False,
            'total_solve_time': total_solve_time
        }
        return None, None, solution_status
    
    return best_schedule_df, best_provider_summary_df, best_solution_status