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
            provider_sessions = defaultdict(lambda: defaultdict(int))
            
            for day in sorted(calendar.keys()):
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
            
            # Create provider summary DataFrame
            all_providers = list(current_config['providers'])
            all_weeks = sorted(set(week for provider_weeks in provider_sessions.values()
                                 for week in provider_weeks))
            
            summary_data = []
            for provider in all_providers:
                provider_data = {'provider': provider}
                total_sessions = 0
                
                for week in all_weeks:
                    week_num = week[1]
                    sessions = provider_sessions[provider][week]
                    provider_data[f'week_{week_num}'] = sessions
                    total_sessions += sessions
                    
                provider_data['total_sessions'] = total_sessions
                summary_data.append(provider_data)
                
            provider_summary_df = pd.DataFrame(summary_data)
            
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
        Path to pediatric YAML config file

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
    from constraints.pediatric import (
        create_shift_variables,
        add_leave_constraints,
        add_inpatient_block_constraints,
        add_call_constraints,
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
                                                    calendar, 
                                                    leave_df, 
                                                    inpatient_days_df, 
                                                    inpatient_starts_df, 
                                                    config['clinic_rules'])
        
        objective_terms.extend(call_objective_terms)
        
        add_post_call_afternoon_constraints(model, 
                                            shift_vars, 
                                            calendar)
        
        # Add clinic workload constraints
        rdo_penalty_terms = add_rdo_constraints(model, 
                                                shift_vars, 
                                                leave_df, 
                                                inpatient_days_df, 
                                                config['clinic_rules'], 
                                                config['providers'])
        
        objective_terms.extend(rdo_penalty_terms) 

        clinic_objective_terms = add_clinic_count_constraints(model, 
                                                              shift_vars, 
                                                              config['providers'], 
                                                              inpatient_starts_df)
        
        objective_terms.extend(clinic_objective_terms)
        
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
            
            # Separate tracking for clinic sessions vs call
            clinic_sessions = defaultdict(lambda: defaultdict(int))
            call_sessions = defaultdict(lambda: defaultdict(int))
            
            for day in sorted(calendar.keys()):
                date_obj = day
                day_of_week = date_obj.strftime('%A')
                
                # For clinic, use standard ISO week (Monday-based)
                clinic_week_key = (date_obj.year, date_obj.isocalendar()[1])
                
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
                    
                    # Process differently based on session type
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
                    else:  # morning or afternoon clinic only
                        # Update clinic counts
                        for provider in scheduled:
                            clinic_sessions[provider][clinic_week_key] += 1
                    
                    # Add to main schedule data (both clinic and call)
                    schedule_data.append({
                        'date': day,
                        'day_of_week': day_of_week,
                        'sessions': session,
                        'providers': ','.join(scheduled),
                        'count': len(scheduled)
                    })
            
            # Convert to DataFrames
            schedule_df = pd.DataFrame(schedule_data)
            schedule_df.loc[schedule_df['sessions'] == 'call', 'count'] = np.nan
            
            # Create provider summary DataFrame - with separate clinic and call sections
            all_providers = list(current_config['providers'])
            
            # Get all unique weeks
            clinic_weeks = sorted(set(week for provider_weeks in clinic_sessions.values()
                                     for week in provider_weeks))
            call_weeks = sorted(set(week for provider_weeks in call_sessions.values()
                                   for week in provider_weeks))
            
            summary_data = []
            for provider in all_providers:
                provider_data = {'provider': provider}
                total_clinic = 0
                
                # Add clinic sessions by week (morning/afternoon only)
                for week in clinic_weeks:
                    week_num = week[1]
                    clinic_count = clinic_sessions[provider][week]
                    provider_data[f'week_{week_num}'] = clinic_count
                    total_clinic += clinic_count
                
                # Add total clinic sessions
                provider_data['total_clinic'] = total_clinic
                
                summary_data.append(provider_data)
                
            provider_summary_df = pd.DataFrame(summary_data)
            
            # Create separate call summary DataFrame
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
            clinic_only_df = schedule_df[schedule_df['sessions'].isin(['morning', 'afternoon'])]
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
        add_pediatric_constraints,
        add_clinic_count_constraints, 
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
        add_pediatric_constraints(model, 
                                  shift_vars, 
                                  peds_schedule_df)

        clinic_objective_terms = add_clinic_count_constraints(model, 
                                                              shift_vars, 
                                                              current_config['providers'], 
                                                              inpatient_starts_df,
                                                              peds_schedule_df)
        objective_terms.extend(clinic_objective_terms)
        
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
            provider_sessions = defaultdict(lambda: defaultdict(int))
            
            for day in sorted(calendar.keys()):
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
            
            # Create provider summary DataFrame
            all_providers = list(current_config['providers'])
            all_weeks = sorted(set(week for provider_weeks in provider_sessions.values()
                                 for week in provider_weeks))
            
            summary_data = []
            for provider in all_providers:
                provider_data = {'provider': provider}
                total_sessions = 0
                
                for week in all_weeks:
                    week_num = week[1]
                    sessions = provider_sessions[provider][week]
                    provider_data[f'week_{week_num}'] = sessions
                    total_sessions += sessions
                    
                provider_data['total_sessions'] = total_sessions
                summary_data.append(provider_data)
                
            provider_summary_df = pd.DataFrame(summary_data)
            
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