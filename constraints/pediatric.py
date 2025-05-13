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
        Dictionary from generate_pediatric_calendar().
        Keys are datetime.date, values are lists of valid sessions (e.g., ['morning', 'afternoon', 'call']).

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

def add_call_constraints(model,
                         shift_vars,
                         calendar,
                         leave_df,
                         inpatient_days_df,
                         inpatient_starts_df, 
                         clinic_rules):
    """
    Enforces pediatric call scheduling constraints:
    - No call the day before or the day of leave
    - No call while on inpatient duty
    - For weeks with federal holidays:
        - If holiday is M-Th, same provider takes call on day before and day of holiday
        - If holiday is Fri, same provider takes call on day before holiday and following Sunday
    - No back-to-back call nights (unless during holiday weeks with M-Th holiday)
    - Penalty for assigning same provider to call twice in a week (unless holiday coverage)

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    calendar : dict
        Dictionary from generate_pediatric_calendar().
        Keys are datetime.date, values are lists of valid sessions.

    leave_df : pd.DataFrame
        Leave request data with columns ['provider', 'date'].

    inpatient_days_df : pd.DataFrame
        Inpatient assignment data with columns ['provider', 'date'].

    inpatient_starts_df : pd.DataFrame
        Inpatient start assignment with columns ['provider', 'start_date'].

    clinic_rules : dict
        Parsed clinic-level rules from the YAML.
    """

    # Initialize objective terms for soft constraints
    objective_terms = []
    
    # Extract holiday dates
    holiday_dates = set(clinic_rules.get('holiday_dates', []))
    
    # Create a mapping of dates to providers on leave
    leave_dates = defaultdict(set)
    for _, row in leave_df.iterrows():
        provider = row['provider']
        leave_date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        
        # Block call on leave date
        leave_dates[leave_date].add(provider)
        
        # Block call on day before leave
        day_before_leave = leave_date - timedelta(days=1)
        leave_dates[day_before_leave].add(provider)
    
    # Create a mapping of dates to providers on inpatient duty
    inpatient_dates = defaultdict(set)
    for _, row in inpatient_days_df.iterrows():
        provider = row['provider']
        inpatient_date = row['date'].date() if hasattr(row['date'], 'date') else row['date']

        # Block call on inpatient dates
        inpatient_dates[inpatient_date].add(provider)

    # Use inpatient_starts_df to block call on Sunday and Monday before inpatient
    # and Thursday and Friday after inpatient ends
    for _, row in inpatient_starts_df.iterrows():
        provider = row['provider']
        inpatient_start = row['start_date'].date() if hasattr(row['start_date'], 'date') else row['start_date']
        
        # Block Sunday before inpatient (2 days before start)
        sunday_before = inpatient_start - timedelta(days = 2)
        inpatient_dates[sunday_before].add(provider)
        
        # Block Monday before inpatient (1 day before start)
        monday_before = inpatient_start - timedelta(days = 1)
        inpatient_dates[monday_before].add(provider)
         
        # Block Thursday after inpatient (9 days after start)
        thursday_after = inpatient_start + timedelta(days = 9)
        inpatient_dates[thursday_after].add(provider)
        
        # Block Friday after inpatient (10 days after start)
        friday_after = inpatient_start + timedelta(days = 10)
        inpatient_dates[friday_after].add(provider)

    # Create a mapping of eligible dates for call
    call_vars = defaultdict(dict)
    for provider in shift_vars:
        for date in shift_vars[provider]:
            # Check if "call" is a session for this date
            if 'call' in shift_vars[provider][date]:
                if provider not in call_vars[date]:
                    call_vars[date][provider] = shift_vars[provider][date]['call']

    # Rule 1 & 2: Block call for providers on leave or inpatient duty
    for date, providers in call_vars.items():
        for provider in providers:
            # Block call on leave dates
            if provider in leave_dates[date]:
                model.Add(call_vars[date][provider] == 0)
            
            # Block call on inpatient dates
            if provider in inpatient_dates[date]:
                model.Add(call_vars[date][provider] == 0)

    # Helper function to get the week key
    def get_week_key(d):
        return (d.year, d.isocalendar()[1])

    # Find dates where call sessions exist, sorted by date
    call_dates = sorted(call_vars.keys())
    
    # Create a mapping of week to dates with call
    week_to_dates = defaultdict(list)
    for d in call_dates:
        week_key = get_week_key(d)
        week_to_dates[week_key].append(d)
    
    # Process each week
    for week_key, dates in week_to_dates.items():
        # Find holidays in this week
        week_holidays = [d for d in holiday_dates if get_week_key(d) == week_key]
        
        # Rule 3: Special holiday call scheduling
        if week_holidays:
            for holiday in week_holidays:
                day_of_week = holiday.strftime('%A')
                
                # For M-Th holidays, same provider does call on day before and day of holiday
                if day_of_week in ['Monday', 'Tuesday', 'Wednesday', 'Thursday']:
                    day_before = holiday - timedelta(days = 1)
                    
                    # Check if both dates have call sessions
                    if day_before in call_vars and holiday in call_vars:
                        for provider in set(call_vars[day_before].keys()) & set(call_vars[holiday].keys()):
                            # Create indicator that provider takes call on day before
                            takes_before = call_vars[day_before][provider]
                            
                            # Create indicator that provider takes call on holiday
                            takes_holiday = call_vars[holiday][provider]
                            
                            # If provider takes call on day before, must take it on holiday too
                            model.Add(takes_holiday == 1).OnlyEnforceIf(takes_before)
                            model.Add(takes_before == 1).OnlyEnforceIf(takes_holiday)
                
                # For Friday holidays, same provider does call on Thursday and Sunday
                elif day_of_week == 'Friday':
                    thursday = holiday - timedelta(days = 1)
                    sunday_after = holiday + timedelta(days = 2)
                    
                    # Check if both dates have call sessions
                    if thursday in call_vars and sunday_after in call_vars:
                        for provider in set(call_vars[thursday].keys()) & set(call_vars[sunday_after].keys()):
                            # Create indicator that provider takes call on Thursday
                            takes_thursday = call_vars[thursday][provider]
                            
                            # Create indicator that provider takes call on Sunday
                            takes_sunday = call_vars[sunday_after][provider]
                            
                            # If provider takes call on Thursday, must take it on Sunday too
                            model.Add(takes_sunday == 1).OnlyEnforceIf(takes_thursday)
                            model.Add(takes_thursday == 1).OnlyEnforceIf(takes_sunday)
            
            # Skip back-to-back checks for holiday weeks with M-Th holidays
            mon_to_thu_holiday = any(h.strftime('%A') in ['Monday', 'Tuesday', 'Wednesday', 'Thursday'] 
                                     for h in week_holidays)
            if mon_to_thu_holiday:
                continue

        # Rule 4: No back-to-back call nights (except during holiday weeks with M-Th holiday)
        for i in range(len(dates) - 1):
            curr_date = dates[i]
            next_date = dates[i + 1]
            
            # Check if consecutive days
            if (next_date - curr_date).days == 1:
                for provider in set(call_vars[curr_date].keys()) & set(call_vars[next_date].keys()):
                    # Create indicator variables
                    takes_curr = call_vars[curr_date][provider]
                    takes_next = call_vars[next_date][provider]
                    
                    # No back-to-back calls
                    model.Add(takes_curr + takes_next <= 1)
    
    # Handle week transitions (Sunday to Monday)
    sorted_week_keys = sorted(week_to_dates.keys())
    for i in range(len(sorted_week_keys) - 1):
        current_week = sorted_week_keys[i]
        next_week = sorted_week_keys[i + 1]
        
        # Get the last day of current week (if it exists)
        if week_to_dates[current_week]:
            last_day_current = max(week_to_dates[current_week])
            
            # Get the first day of next week (if it exists)
            if week_to_dates[next_week]:
                first_day_next = min(week_to_dates[next_week])
                
                # Check if these are consecutive days (Sunday-Monday transition)
                if (first_day_next - last_day_current).days == 1:
                    # Check if this is a holiday transition that should be excluded
                    is_holiday_transition = False
                    
                    # If last day is Sunday and first day is a Monday holiday, 
                    # check for Friday holiday exception
                    if (last_day_current.strftime('%A') == 'Sunday' and 
                        first_day_next.strftime('%A') == 'Monday' and
                        first_day_next in holiday_dates):
                        # Find if there was a Friday holiday in the previous week
                        friday_before = last_day_current - timedelta(days=2)
                        if friday_before in holiday_dates:
                            is_holiday_transition = True
                    
                    # If not a special holiday transition, enforce no back-to-back
                    if not is_holiday_transition:
                        for provider in set(call_vars[last_day_current].keys()) & set(call_vars[first_day_next].keys()):
                            # Create indicator variables
                            takes_last = call_vars[last_day_current][provider]
                            takes_first = call_vars[first_day_next][provider]
                            
                            # No back-to-back calls across week boundaries
                            model.Add(takes_last + takes_first <= 1)
    
    # Rule 5: Penalty for same provider having call twice in a "call week" (Sunday-Thursday)
    # Group call dates into "call weeks" that run Sunday-Thursday
    call_weeks = defaultdict(list)
    
    for call_date in sorted(call_vars.keys()):
        day_of_week = call_date.strftime('%A')
        if day_of_week in ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday']:
            # For grouping, use the date of the Sunday that starts this "call week"
            if day_of_week == 'Sunday':
                week_start = call_date
            else:
                # Calculate the Sunday that started this week
                days_since_sunday = {'Monday': 1, 'Tuesday': 2, 'Wednesday': 3, 'Thursday': 4}
                week_start = call_date - timedelta(days=days_since_sunday[day_of_week])
            
            # Add to the appropriate call week
            call_weeks[week_start].append(call_date)
    
    # Process each "call week" (Sunday-Thursday)
    for week_start, dates in call_weeks.items():
        # Find holidays in this call week
        call_week_holidays = [d for d in holiday_dates if d in dates]
        
        # Get all providers who could take call this week
        week_providers = set()
        for d in dates:
            week_providers.update(call_vars[d].keys())
        
        # For each provider, create penalty for multiple call assignments
        for provider in week_providers:
            # Collect valid call variables for this provider in this call week
            provider_call_vars = [
                call_vars[d][provider] for d in dates 
                if provider in call_vars[d]
            ]
            
            # Skip if fewer than 2 possible call days
            if len(provider_call_vars) < 2:
                continue
            
            # Create penalty for more than one call assignment
            penalty_var = model.NewIntVar(0, len(provider_call_vars) - 1, 
                                         f"multiple_call_penalty_{provider}_{week_start.isoformat()}")
            
            # Sum of calls should be <= 1 + penalty
            model.Add(sum(provider_call_vars) <= 1 + penalty_var)
            
            # Add penalty to objective with appropriate weight
            # (unless this is for holiday coverage which is enforced by Rule 3)
            if not call_week_holidays:
                objective_terms.append(penalty_var * 100) 
    
    return objective_terms

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
    
    The minimum target is the same as the maximum by default to provide flexibility. For additional
    flexibility, make minimum target 1 less than maximum target.
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
            target_min_clinics = max(1, target_max_clinics)
            #target_min_clinics = max(1, target_max_clinics - 1) for more flexibility   
            
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

