from ortools.sat.python import cp_model
from datetime import timedelta
from collections import defaultdict

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

def add_clinic_count_constraints(model,
                                 shift_vars,
                                 provider_config,
                                 inpatient_starts_df):
    """
    Enforces weekly clinic session targets per provider, based on max_clinics_per_week. 

    Implements a combination of hard and soft constraints:
    - Hard constraint: Providers cannot exceed their maximum allowed clinics per week
      (strictly enforced, cannot be violated)
    - Soft constrint: Providers should achieve their minimum target of clinics per week
      (enforced with penalties, can be violated if necessary for feasibility)
    
    The minimum target is set to be 1 less than the maximum by default to provide flexibility.
    For post-inpatient weeks, both maximum and minimum targets are reduced by 2 sessions.
    
    Soft constraints use penalty variables (range 0-10) with a weight of 100 in the objective
    function, allowing the solver to find feasible solutions even when perfect solutions
    don't exist, while strongly discouraging violations.

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
    """
    # Initialize objective terms list if not already done elsewhere
    objective_terms = []
    
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
            # Calculate target
            target_max_clinics = base_max_clinics
            if week_key in post_inpatient_weeks[provider]:
                target_max_clinics = max(0, target_max_clinics - 2)
            
            # Max constraint remains hard
            model.Add(sum(var_list) <= target_max_clinics)
            
            # Min constraint becomes soft with penalty
            target_min_clinics = max(1, target_max_clinics - 1)  # Allow some flexibility
            
            # Create penalty variable for under-minimum
            under_min = model.NewIntVar(0, 10, f"under_min_{provider}_{week_key}")
            model.Add(sum(var_list) + under_min >= target_min_clinics)
            
            # Add penalty to objective terms
            objective_terms.append(under_min * 100)  # Weight of 100
    
    # Return objective terms to be added to the model's objective
    return objective_terms

def add_rdo_constraints(model, 
                        shift_vars, 
                        leave_df, 
                        inpatient_days_df, 
                        clinic_rules, 
                        provider_config):
    """
    Enforces 1 RDO per week for each provider, accounting for special cases:
      - MD/DOs do NOT get RDO during a holiday week
      - NP/PAs DO get RDO even during holiday weeks
      - Any provider with inpatient or leave that week does NOT get additional RDO
      - RDO must occur on an eligible weekday (e.g., Mon/Tue/Wed/Fri)
      - If a provider has an rdo_preference set in YAML, it must occur on that day

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
    """
    # Extract configuration
    holiday_dates = set(clinic_rules.get('holiday_dates', []))
    eligible_days = set(clinic_rules.get('random_day_off', {}).get('eligible_days', []))
    provider_roles = {p: info['role'] for p, info in provider_config.items()}
    rdo_preferences = {p: info.get('rdo_preference') for p, info in provider_config.items()}

    # Define week key function with proper year boundary handling
    def get_week_key(d): 
        return (d.year, d.isocalendar()[1])

    # Identify weeks where providers don't get RDOs
    # (due to inpatient, leave, or MD holiday)
    blocked_weeks = defaultdict(set)
    
    # Block weeks where provider has leave
    for _, row in leave_df.iterrows():
        provider = row['provider']
        week = get_week_key(row['date'])
        blocked_weeks[provider].add(week)
    
    # Block weeks where provider has inpatient duty
    for _, row in inpatient_days_df.iterrows():
        provider = row['provider']
        week = get_week_key(row['date'])
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
                # Check if the date has shift variables
                if date in shift_vars[provider]:
                    # Variable name includes date for uniqueness
                    is_rdo = model.NewBoolVar(f"{provider}_RDO_{date.isoformat()}")
                    
                    # Sum all sessions for this day
                    day_sessions = list(shift_vars[provider][date].values())
                    
                    # A day is an RDO if all sessions are 0
                    model.Add(sum(day_sessions) == 0).OnlyEnforceIf(is_rdo)
                    model.Add(sum(day_sessions) > 0).OnlyEnforceIf(is_rdo.Not())
                    
                    rdo_indicators.append(is_rdo)
            
            # Require exactly one RDO day per week (if eligible days exist)
            if rdo_indicators:
                model.Add(sum(rdo_indicators) == 1)

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

