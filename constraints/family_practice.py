from ortools.sat.python import cp_model
from datetime import timedelta
from collections import defaultdict
import pandas as pd

def create_shift_variables(model, 
                           providers, 
                           calendar):
    """
    Create binary decision variables for each (provider, date, session). 
    
    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.
    
    providers : list of str
        List of provider names.
    
    calendar : dict
        Dictionary from generate_clinic_calendar().
        Keys are datetime.date, values are lists of valid sessions (e.g., ['morning', 'afternoon']).

    Returns:
    -------
    dict
        A nested dict: shift_vars[provider][date][session] = BoolVar.
    """
    shift_vars = {}

    for provider in providers:
        shift_vars[provider] = {}
        for day, sessions in calendar.items():
            shift_vars[provider][day] = {}
            for session in sessions:
                var_name = f"{provider}_{day.isoformat()}_{session}"
                shift_vars[provider][day][session] = model.NewBoolVar(var_name)

    return shift_vars

def add_leave_constraints(model, 
                          shift_vars, 
                          leave_df):
    """
    Blocks clinic assignments for providers on requested leave days.

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    leave_df : pd.DataFrame
        DataFrame with leave requests. Must contain 'provider' and 'date' columns.
    """
    for _, row in leave_df.iterrows():
        provider = row['provider']
        date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        for session in shift_vars.get(provider, {}).get(date, {}):
            model.Add(shift_vars[provider][date][session] == 0)

def add_inpatient_block_constraints(model,
                                    shift_vars,
                                    inpatient_starts_df,
                                    inpatient_days_df):
    """
    Blocks clinic sessions for providers during inpatient weeks:
    - Monday before inpatient (pre_inpatient_leave)
    - Tuesday to Monday of inpatient week
    - Friday after inpatient (post_inpatient_leave)

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    inpatient_starts_df : pd.DataFrame
        DataFrame with ['provider', 'start_date'] indicating start of inpatient week.

    inpatient_days_df : pd.DataFrame
        Expanded DataFrame with columns ['provider', 'date'] for all inpatient dates.
    """
    blocked_dates = defaultdict(set)

    # Block pre and post inpatient leave days
    for _, row in inpatient_starts_df.iterrows():
        provider = row['provider']
        start_date = row['start_date']

        # Block the day before inpatient starts (typically Monday)
        pre_leave_date = start_date - timedelta(days=1)
        blocked_dates[provider].add(pre_leave_date)
        
        # Block the Friday after inpatient ends
        post_leave_date = start_date + timedelta(days=10)
        blocked_dates[provider].add(post_leave_date) 

    # Block all inpatient dates (Tuesday–Monday inclusive)
    for _, row in inpatient_days_df.iterrows():
        provider = row['provider']
        d = row['date']
        blocked_dates[provider].add(d)

    # Apply constraints
    for provider, dates in blocked_dates.items():
        for d in dates:
            for session in shift_vars.get(provider, {}).get(d, {}):
                model.Add(shift_vars[provider][d][session] == 0)

def add_pediatric_constraints(model,
                              shift_vars,
                              peds_schedule_df):
    """
    Adds constraints for providers who also have pediatric assignments:
    - No afternoon clinic assignments the day after pediatric call
    - No clinic assignments during pediatric clinic sessions
    
    Note: Evening call does NOT block same-day clinic sessions, as call occurs
    in the evening after clinic hours have ended.

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    peds_schedule_df : pd.DataFrame
        Pediatric schedule DataFrame. Must have columns ['date', 'sessions', 'providers'].
    """
    if peds_schedule_df is None:
        return
        
    # Process all pediatric assignments (both call and clinic)
    for _, row in peds_schedule_df.iterrows():
        date = row['date']
        session = row['session']
        providers = row['providers'].split(',') if isinstance(row['providers'], str) else []
        
        for provider in providers:
            provider = provider.strip()
            
            # Skip if provider isn't in our scheduler
            if provider not in shift_vars:
                continue
                
            # Handle based on session type
            if session == 'call':
                # Block afternoon session on the day AFTER call (post-call restriction)
                next_day = date + timedelta(days=1)
                if (next_day in shift_vars[provider] and 
                    'afternoon' in shift_vars[provider][next_day]):
                    model.Add(shift_vars[provider][next_day]['afternoon'] == 0)
            
            # For morning/afternoon clinic sessions in pediatrics, block the same session in family practice
            elif session in ['morning', 'afternoon']:
                if (date in shift_vars[provider] and 
                    session in shift_vars[provider][date]):
                    model.Add(shift_vars[provider][date][session] == 0)

def add_clinic_count_constraints(model,
                                 shift_vars,
                                 provider_config,
                                 inpatient_starts_df,
                                 peds_schedule_df = None):
    """
    Enforces weekly clinic session targets per provider, based on max_clinics_per_week.
    
    For family practice provider who also work clinic in pediatrics, it factors this
    when assigning family practice clinic sessions. 

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model. 

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    provider_config : dict
        Parsed provider-level information from the YAML (eg., config['providers']). 

    inpatient_starts_df : pd.DataFrame
        Parsed DataFrame from inpatient.csv with columns ['provider', 'start_date'], where start_date is a 
        datetime.date.
        
    peds_schedule_df : pd.DataFrame, optional
        Pediatric schedule DataFrame with columns ['date', 'sessions', 'providers']. Used to track
        pediatric clinic assignments for shared providers.
    """
    # Initialize objective terms list if not already done elsewhere
    objective_terms = []
    
    # Create a mapping of pediatric clinic sessions by provider and week
    peds_clinics_by_provider_week = defaultdict(lambda: defaultdict(int))
    
    if peds_schedule_df is not None:
        # Process pediatric schedule to track clinic sessions (not call)
        for _, row in peds_schedule_df.iterrows():
            date = row['date']
            session = row['session']
            providers = row['providers'].split(',') if isinstance(row['providers'], str) else []
            
            # Only count morning and afternoon clinic sessions (not call)
            if session in ['morning', 'afternoon']:
                # Get the ISO week for weekly tracking
                week_key = (date.year, date.isocalendar()[1])
                
                # Count session for each assigned provider
                for provider in providers:
                    provider = provider.strip()
                    peds_clinics_by_provider_week[provider][week_key] += 1
    
    # Identify post-inpatient weeks for clinic reduction
    post_inpatient_weeks = defaultdict(set)
    for _, row in inpatient_starts_df.iterrows():
        provider = row['provider']
        inpatient_end = row['start_date'] + timedelta(days=6)  # Monday
        post_week_tuesday = inpatient_end + timedelta(days=1)  # Tuesday
        week_key = (post_week_tuesday.year, post_week_tuesday.isocalendar()[1])
        post_inpatient_weeks[provider].add(week_key)

    # Group shifts by provider and week
    weekly_shifts = defaultdict(lambda: defaultdict(list))
    for provider in shift_vars:
        for date in shift_vars[provider]:
            week_key = (date.year, date.isocalendar()[1])
            for session in shift_vars[provider][date]:
                weekly_shifts[provider][week_key].append(shift_vars[provider][date][session])

    # Apply constraints for each provider/week
    for provider, weeks in weekly_shifts.items():
        if provider not in provider_config:
            continue
            
        base_max_clinics = provider_config[provider]['max_clinics_per_week']

        for week_key, var_list in weeks.items():
            # Calculate target considering both post-inpatient adjustments and pediatric sessions
            target_max_clinics = base_max_clinics
            
            # Reduce for post-inpatient weeks
            if week_key in post_inpatient_weeks[provider]:
                target_max_clinics = max(0, target_max_clinics - 2)
                
            # Count how many pediatric clinic sessions this provider has this week
            peds_sessions_this_week = peds_clinics_by_provider_week[provider][week_key]
            
            # If this is a shared provider, the max clinic limit applies to the total
            # across both departments. Reduce the family practice max by the pediatric sessions.
            # For shared providers, make sure their combined total is <= base_max_clinics
            adjusted_max_clinics = max(0, target_max_clinics - peds_sessions_this_week)
            
            # Max constraint remains hard
            model.Add(sum(var_list) <= adjusted_max_clinics)
            
            # Min constraint is adjusted as well - ensure we don't require impossible values
            adjusted_min_clinics = max(0, adjusted_max_clinics)
            
            # For providers with very few sessions left (1 or 0) after accounting for pediatrics,
            # make the min constraint 0 to ensure flexibility
            if adjusted_max_clinics <= 1:
                adjusted_min_clinics = 0
            
            # Create penalty variable for under-minimum
            under_min = model.NewIntVar(0, 10, f"under_min_{provider}_{week_key}")
            model.Add(sum(var_list) + under_min >= adjusted_min_clinics)
            
            # Add penalty to objective terms
            objective_terms.append(under_min * 100)  # Weight of 100
    
    # Return objective terms to be added to the model's objective
    return objective_terms

def add_fracture_clinic_constraints(model,
                                    shift_vars,
                                    calendar,
                                    provider_config):
    """
    Adds soft constraints to ensure fracture clinic coverage on Wednesdays.
    
    Penalty is applied when neither fracture clinic provider is scheduled for 
    both morning AND afternoon sessions on Wednesdays.
    
    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.
    
    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].
    
    calendar : dict
        Dictionary from generate_clinic_calendar().
        Keys are datetime.date, values are lists of valid sessions.
    
    provider_config : dict
        Parsed provider-level configuration with fracture_clinic flags.
    
    Returns:
    -------
    list
        List of penalty variables to be added to the objective function.
    """
    # Initialize penalty terms
    objective_terms = []
    
    # Identify fracture clinic providers
    fracture_providers = [
        provider for provider, config in provider_config.items()
        if config.get('fracture_clinic', False)
    ]
    
    if len(fracture_providers) < 2:
        # If we don't have 2 fracture providers, skip this constraint
        return objective_terms
    
    # Find all Wednesdays in the calendar
    wednesdays = [
        date for date in calendar.keys()
        if date.strftime('%A') == 'Wednesday'
    ]
    
    for wednesday in wednesdays:
        # Check if this Wednesday has both morning and afternoon sessions
        if ('morning' not in calendar[wednesday] or 
            'afternoon' not in calendar[wednesday]):
            continue
        
        # Create variables for each fracture provider working full day
        provider_full_day_vars = []
        
        for provider in fracture_providers:
            # Skip if provider doesn't have shift variables for this day
            if (provider not in shift_vars or 
                wednesday not in shift_vars[provider]):
                continue
            
            # Check if both morning and afternoon sessions exist for this provider
            if ('morning' not in shift_vars[provider][wednesday] or
                'afternoon' not in shift_vars[provider][wednesday]):
                continue
            
            # Create variable indicating this provider works full day
            provider_full_day = model.NewBoolVar(
                f"{provider}_full_day_{wednesday.isoformat()}"
            )
            
            # Get morning and afternoon variables
            morning_var = shift_vars[provider][wednesday]['morning']
            afternoon_var = shift_vars[provider][wednesday]['afternoon']
            
            # provider_full_day is 1 if and only if both morning and afternoon are 1
            model.AddBoolAnd([morning_var, afternoon_var]).OnlyEnforceIf(provider_full_day)
            model.AddBoolOr([morning_var.Not(), afternoon_var.Not()]).OnlyEnforceIf(provider_full_day.Not())
            
            provider_full_day_vars.append(provider_full_day)
        
        # If we have at least one provider who could work full day
        if provider_full_day_vars:
            # Create penalty variable for this Wednesday
            penalty_var = model.NewBoolVar(f"fracture_penalty_{wednesday.isoformat()}")
            
            # At least one provider should work full day
            at_least_one_full_day = model.NewBoolVar(f"at_least_one_full_day_{wednesday.isoformat()}")
            model.AddBoolOr(provider_full_day_vars).OnlyEnforceIf(at_least_one_full_day)
            model.AddBoolAnd([var.Not() for var in provider_full_day_vars]).OnlyEnforceIf(at_least_one_full_day.Not())
            
            # Penalty is 1 when no provider works full day
            model.Add(penalty_var == at_least_one_full_day.Not())
            
            # Add to penalty terms with weight of 100
            objective_terms.append(penalty_var * 100)
    
    return objective_terms

def add_rdo_constraints(model, 
                        shift_vars, 
                        leave_df, 
                        inpatient_days_df, 
                        clinic_rules, 
                        provider_config,
                        peds_schedule_df=None):
    """
    Enforces 1 RDO per week for each provider, accounting for special cases:
      - MD/DOs do NOT get RDO during a holiday week
      - NP/PAs DO get RDO even during holiday weeks
      - Any provider with inpatient or leave that week does NOT get additional RDO
      - RDO must occur on an eligible weekday (e.g., Mon/Tue/Wed/Fri)
      - If a provider has an rdo_preference set in YAML, it must occur on that day
      - Special handling for providers also working in pediatrics (Powell and Shin):
        - RDO cannot be assigned on pediatric clinic days
        - RDO cannot be assigned on pediatric call days (per call rules)
        - Penalize RDO on the day after pediatric call

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    leave_df : pd.DataFrame
        Leave request data. Must have columns ['provider', 'date'].

    inpatient_days_df : pd.DataFrame
        Inpatient assignment days. Must have columns ['provider', 'date'].

    clinic_rules : dict
        Parsed clinic-level rules from the YAML.

    provider_config : dict
        Parsed provider-level information from the YAML (eg., config['providers']).
        
    peds_schedule_df : pd.DataFrame, optional
        Pediatric schedule DataFrame. Must have columns ['date', 'sessions', 'providers'].
        Used to identify pediatric call and clinic days for shared providers like Powell and Shin.
    """
    # Initialize a list for penalty variables
    rdo_penalty_vars = []
    
    # Extract configuration
    holiday_dates = set(clinic_rules.get('holiday_dates', []))
    eligible_days = set(clinic_rules.get('random_day_off', {}).get('eligible_days', []))
    provider_roles = {p: info['role'] for p, info in provider_config.items()}
    rdo_preferences = {p: info.get('rdo_preference') for p, info in provider_config.items()}

    # Define week key function
    def get_week_key(d): 
        return (d.year, d.isocalendar()[1])

    # Create mappings for pediatric busy dates
    peds_clinic_dates = defaultdict(set)  # Dates with pediatric clinic sessions
    peds_call_dates = defaultdict(set)    # Dates with pediatric call
    
    if peds_schedule_df is not None:
        # Process pediatric schedule to track both call and clinic assignments
        for _, row in peds_schedule_df.iterrows():
            date = row['date']
            session = row['session']
            providers_str = row['providers'] if not pd.isna(row['providers']) else ''
            providers = providers_str.split(',') if providers_str else []
            
            for provider in providers:
                provider = provider.strip()
                
                if session == 'call':
                    # Add to call dates (call rules indicate no RDO on call days)
                    peds_call_dates[provider].add(date)
                elif session in ['morning', 'afternoon']:
                    # Add to clinic dates
                    peds_clinic_dates[provider].add(date)
    
    # Identify weeks where providers don't get RDOs
    blocked_weeks = defaultdict(set)
    
    # Block weeks where provider has leave
    for _, row in leave_df.iterrows():
        provider = row['provider']
        date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        week = get_week_key(date)
        blocked_weeks[provider].add(week)
    
    # Block weeks where provider has inpatient duty
    for _, row in inpatient_days_df.iterrows():
        provider = row['provider']
        date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        week = get_week_key(date)
        blocked_weeks[provider].add(week)
    
    # Block holiday weeks for MD/DO providers only
    for holiday_date in holiday_dates:
        holiday_week = get_week_key(holiday_date)
        for provider, role in provider_roles.items():
            if role in ['MD', 'DO']:
                blocked_weeks[provider].add(holiday_week)

    # Organize dates by provider and week, keeping only eligible days
    eligible_dates_by_provider_week = defaultdict(lambda: defaultdict(list))
    
    for provider in shift_vars:
        for date in shift_vars[provider]:
            # Only consider days that are eligible for RDO
            day_of_week = date.strftime('%A')
            if day_of_week in eligible_days:
                week = get_week_key(date)
                eligible_dates_by_provider_week[provider][week].append(date)

    # Apply RDO constraints for each provider and week
    for provider, weeks_data in eligible_dates_by_provider_week.items():
        for week, eligible_dates in weeks_data.items():
            # Skip blocked weeks (providers don't get RDO)
            if week in blocked_weeks[provider]:
                continue
                
            # Apply RDO preference if specified
            preferred_day = rdo_preferences.get(provider)
            if preferred_day:
                preferred_dates = [d for d in eligible_dates if d.strftime('%A') == preferred_day]
                # Only use preferred days if available, otherwise fall back to all eligible days
                if preferred_dates:
                    eligible_dates = preferred_dates
                    
            # Skip if no eligible days for RDO in this week
            if not eligible_dates:
                continue
                
            # Create RDO indicators for each eligible day
            rdo_indicators = []
            for date in eligible_dates:
                # Skip dates with pediatric clinic or call sessions
                if date in peds_clinic_dates[provider] or date in peds_call_dates[provider]:
                    continue
                    
                # Check if the date has shift variables 
                if date in shift_vars[provider]:
                    # Variable name includes date for uniqueness
                    is_rdo = model.NewBoolVar(f"{provider}_RDO_{date.isoformat()}")
                    
                    # Sum all sessions for this day
                    day_sessions = list(shift_vars[provider][date].values())
                    
                    # A day is an RDO if all sessions are 0
                    model.Add(sum(day_sessions) == 0).OnlyEnforceIf(is_rdo)
                    model.Add(sum(day_sessions) > 0).OnlyEnforceIf(is_rdo.Not())
                    
                    # SOFT CONSTRAINT: Penalize RDO the day after pediatric call
                    previous_day = date - timedelta(days=1)
                    if previous_day in peds_call_dates[provider]:
                        # Create a penalty variable for post-call RDO
                        post_call_rdo = model.NewBoolVar(f"{provider}_post_call_rdo_{date.isoformat()}")
                        
                        # Add a constraint that links the penalty variable to is_rdo
                        model.Add(post_call_rdo == is_rdo)
                        
                        # Add penalty to the collection
                        rdo_penalty_vars.append(post_call_rdo)
                    
                    rdo_indicators.append(is_rdo)
            
            # Require exactly one RDO day per week (if eligible days exist)
            if rdo_indicators:
                model.Add(sum(rdo_indicators) == 1)
                
    # Return penalty variables to be added to the objective function
    return rdo_penalty_vars

def add_pediatric_call_constraints(model,
                                  shift_vars,
                                  peds_schedule_df):
    """
    Adds constraints for providers who also have pediatric assignments:
    - No afternoon clinic assignments the day after pediatric call
    - No clinic assignments during pediatric clinic sessions
    
    Note: Evening call does NOT block same-day clinic sessions, as call occurs
    in the evening after clinic hours have ended.

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    peds_schedule_df : pd.DataFrame
        Pediatric schedule DataFrame. Must have columns ['date', 'sessions', 'providers'].
    """
    if peds_schedule_df is None:
        return
        
    # Process all pediatric assignments (both call and clinic)
    for _, row in peds_schedule_df.iterrows():
        date = row['date']
        session = row['session']
        providers_str = row['providers'] if not pd.isna(row['providers']) else ''
        providers = providers_str.split(',') if providers_str else []
        
        for provider in providers:
            provider = provider.strip()
            
            # Skip if provider isn't in our scheduler
            if provider not in shift_vars:
                continue
                
            # Handle based on session type
            if session == 'call':
                # Call is in the evening, so NO restriction on same-day clinic
                
                # Block afternoon session on the day AFTER call (post-call restriction)
                next_day = date + timedelta(days=1)
                if (next_day in shift_vars[provider] and 
                    'afternoon' in shift_vars[provider][next_day]):
                    model.Add(shift_vars[provider][next_day]['afternoon'] == 0)
            
            # For morning/afternoon clinic sessions in pediatrics, block the same session in family practice
            elif session in ['morning', 'afternoon']:
                if (date in shift_vars[provider] and 
                    session in shift_vars[provider][date]):
                    model.Add(shift_vars[provider][date][session] == 0)

def add_min_max_staffing_constraints(model, 
                                     shift_vars, 
                                     calendar, 
                                     clinic_rules):
    """
    Adds constraints to enforce minimum and maximum number of providers per clinic session.

    Parameters:
    ----------
    model : cp_model.CpModel
        The OR-Tools model.
    
    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    calendar : dict
        Dictionary from generate_clinic_calendar().
        Keys are datetime.date, values are lists of valid sessions (e.g., ['morning', 'afternoon']).

    clinic_rules : dict
        Parsed clinic-level rules from the YAML.
    """
    min_staff = clinic_rules.get('staffing', {}).get('min_providers_per_session', 1)
    max_staff = clinic_rules.get('staffing', {}).get('max_providers_per_session', 5)

    for d, sessions in calendar.items():
        for s in sessions:
            staff_vars = [
                shift_vars[provider][d][s]
                for provider in shift_vars
                if d in shift_vars[provider] and s in shift_vars[provider][d]
            ]
            if staff_vars:
                model.Add(sum(staff_vars) >= min_staff)
                model.Add(sum(staff_vars) <= max_staff)

