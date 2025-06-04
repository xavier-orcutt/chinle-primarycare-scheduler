from ortools.sat.python import cp_model
from datetime import timedelta
from collections import defaultdict

def get_schedule_week_key(d):
    """
    Returns a consistent week key for scheduling purposes, with weeks running Sunday to Saturday.
    
    Parameters:
    ----------
    d : datetime.date
        The date to find the week key for.
        
    Returns:
    -------
    tuple
        A unique identifier for the week: (year, month, day) of the Sunday that starts the week.
    """
    # Find the Sunday that starts this week
    days_since_sunday = d.weekday() + 1 if d.weekday() < 6 else 0
    week_start = d - timedelta(days=days_since_sunday)
    return (week_start.year, week_start.month, week_start.day)

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

    # Apply constraints - ONLY block clinic sessions, not call
    for provider, dates in blocked_dates.items():
        for d in dates:
            for session in shift_vars.get(provider, {}).get(d, {}):
                # Skip call sessions - let add_call_constraints() handle those
                if session != 'call':
                    model.Add(shift_vars[provider][d][session] == 0)

def add_call_constraints(model,
                         shift_vars,
                         leave_df,
                         inpatient_starts_df, 
                         clinic_rules,
                         provider_config):
    """
    Enforces pediatric call scheduling constraints:
    - Exactly one provider on call each day
    - Inpatient pediatrics provider always and only take call Friday and Saturday 
    - No call the day before or the day of leave
    - For weeks with federal holidays:
        - If holiday is M-Th, same provider takes call on day before and day of holiday
        - If holiday is Fri, same provider takes call on day before holiday and following Sunday
    - No back-to-back call nights M-Th unless holiday falls on one of those days
    - Penalty for assigning same provider to call twice Su-Th, unless holiday coverage
    - Fracture clinic providers cannot take call on Tuesdays (to preserve Wednesday availability)

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    calendar : dict
        Dictionary from generate_pediatric_calendar() with call sessions.

    leave_df : pd.DataFrame
        Leave request data with columns ['provider', 'date'].

    inpatient_starts_df : pd.DataFrame
        Inpatient start assignment with columns ['provider', 'start_date', 'inpatient_type'].

    clinic_rules : dict
        Parsed clinic-level rules from the YAML.

    provider_config : dict
        Parsed provider-level information from the YAML (eg., config['providers']). 
    """
    # ========================================================================
    # SECTION A: DATA PREPARATION & SETUP
    # ========================================================================
    # Initialize objective terms for soft constraints
    objective_terms = []
    
    # A1. Extract basic data
    holiday_dates = set(clinic_rules.get('holiday_dates', []))
    
    fracture_providers = [
        provider for provider, config in provider_config.items()
        if config.get('fracture_clinic', False)
    ]
    
    # Get all dates that have call sessions
    call_dates = set()
    for provider in shift_vars:
        for date in shift_vars[provider]:
            if 'call' in shift_vars[provider][date]:
                call_dates.add(date)
    
    # A2. Create blocking sets
    # Leave blocking 
    leave_blocked_dates = defaultdict(set)
    for _, row in leave_df.iterrows():
        provider = row['provider']
        leave_date = row['date']
        
        # Block call on leave date
        leave_blocked_dates[leave_date].add(provider)
        
        # Block call on day before leave
        day_before_leave = leave_date - timedelta(days=1)
        leave_blocked_dates[day_before_leave].add(provider)
    
    inpatient_blocked_dates = defaultdict(set)
    # Process inpatient assignments based on type
    for _, row in inpatient_starts_df.iterrows():
        provider = row['provider']
        inpatient_start = row['start_date']  # This is Tuesday (day 0)
        inpatient_type = row['inpatient_type']
        
        if inpatient_type == 'peds':
            # PEDIATRIC INPATIENT: Block from ALL call on specific days
            # Sunday (-2), Monday (-1), Tuesday (0), Wednesday (1), Thursday (2), 
            # Sunday (5), Monday (6), Thursday (8), Friday (9)
            block_days = [-2, -1, 0, 1, 2, 5, 6, 8, 9]
            for day_offset in block_days:
                block_date = inpatient_start + timedelta(days=day_offset)
                inpatient_blocked_dates[block_date].add(provider)
                
        else:  # Adult inpatient or any other type
            # ADULT INPATIENT: Block from ALL call on all relevant days
            # Sunday (-2), Monday (-1), Tuesday (0), Wednesday (1), Thursday (2), 
            # Friday (3), Saturday (4), Sunday (5), Monday (6), Thursday (8), Friday (9)
            block_days = [-2, -1, 0, 1, 2, 3, 4, 5, 6, 8, 9]
            for day_offset in block_days:
                block_date = inpatient_start + timedelta(days=day_offset)
                inpatient_blocked_dates[block_date].add(provider)
    
    # A3. Create Friday/Saturday assignment mapping
    friday_saturday_assignments = {}  
    
    # Assign pediatric inpatient providers to Friday/Saturday
    for _, row in inpatient_starts_df.iterrows():
        provider = row['provider']
        inpatient_start = row['start_date']  # Tuesday (day 0)
        inpatient_type = row['inpatient_type']
        
        if inpatient_type == 'peds':
            # ASSIGN Friday (3) and Saturday (4)
            friday_date = inpatient_start + timedelta(days=3)
            saturday_date = inpatient_start + timedelta(days=4)
            
            friday_saturday_assignments[friday_date] = provider
            friday_saturday_assignments[saturday_date] = provider

    # ========================================================================
    # SECTION B: HARD CONSTRAINTS
    # ========================================================================
    
    # B1. Exactly one provider on call each day
    for date in call_dates:
        # Get all providers who have call variables for this date
        providers_with_call = [
            provider for provider in shift_vars
            if date in shift_vars[provider] and 'call' in shift_vars[provider][date]
        ]
        
        if providers_with_call:
            # Sum of all call variables for this date must equal 1
            call_vars_for_date = [shift_vars[provider][date]['call'] for provider in providers_with_call]
            model.Add(sum(call_vars_for_date) == 1)
    
    # B2. Friday/Saturday inpatient assignment
    for date, assigned_provider in friday_saturday_assignments.items():
        # The assigned provider MUST take call on this date
        if (assigned_provider in shift_vars and 
            date in shift_vars[assigned_provider] and 
            'call' in shift_vars[assigned_provider][date]):
            model.Add(shift_vars[assigned_provider][date]['call'] == 1)
    
    # B3. Blocking constraints
    for date in call_dates:
        day_of_week = date.strftime('%A')
        
        for provider in shift_vars:
            if date in shift_vars[provider] and 'call' in shift_vars[provider][date]:
                call_var = shift_vars[provider][date]['call']
                
                # B3.1: Block providers on leave (affects ALL call)
                if provider in leave_blocked_dates[date]:
                    model.Add(call_var == 0)
                
                # B3.2: Block inpatient providers
                if provider in inpatient_blocked_dates[date]:
                    model.Add(call_var == 0)
                
                # B3.3: Block fracture clinic providers from Tuesday call
                if provider in fracture_providers and day_of_week == 'Tuesday':
                    model.Add(call_var == 0)

    # ========================================================================
    # SECTION C: CALL RULES FOR SUNDAY-THURSDAY ONLY
    # ========================================================================
    
    # Filter to Sunday-Thursday call dates only 
    sun_thu_call_dates = [
        date for date in call_dates 
        if date.strftime('%A') in ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday']
    ]
    
    # C1. Holiday pairing rules 
    for holiday in holiday_dates:
        holiday_day = holiday.strftime('%A')
            
        # For M-Th holidays: same provider takes call on day before and day of holiday
        if holiday_day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday']:
            day_before = holiday - timedelta(days=1)
            
            # Get providers who can take call on both dates (and aren't blocked)
            providers_both_days = []
            for provider in shift_vars:
                if (day_before in shift_vars[provider] and 'call' in shift_vars[provider][day_before] and
                    holiday in shift_vars[provider] and 'call' in shift_vars[provider][holiday] and
                    # Check if provider is NOT blocked on either day
                    provider not in inpatient_blocked_dates[day_before] and
                    provider not in inpatient_blocked_dates[holiday] and
                    provider not in leave_blocked_dates[day_before] and
                    provider not in leave_blocked_dates[holiday]):
                    providers_both_days.append(provider)
                
            # Enforce same provider constraint
            for provider in providers_both_days:
                takes_before = shift_vars[provider][day_before]['call']
                takes_holiday = shift_vars[provider][holiday]['call']
                    
                # If provider takes call on day before, must take it on holiday too
                model.Add(takes_holiday == takes_before)
        
        # For Friday holidays: same provider takes call on Thursday and following Sunday
        elif holiday_day == 'Friday':
            thursday_before = holiday - timedelta(days=1)
            sunday_after = holiday + timedelta(days=2)
            
            # Get providers who can take call on both dates (and aren't blocked)
            providers_both_days = []
            for provider in shift_vars:
                if (thursday_before in shift_vars[provider] and 'call' in shift_vars[provider][thursday_before] and
                    sunday_after in shift_vars[provider] and 'call' in shift_vars[provider][sunday_after] and
                    # Check if provider is NOT blocked on either day
                    provider not in inpatient_blocked_dates[thursday_before] and
                    provider not in inpatient_blocked_dates[sunday_after] and
                    provider not in leave_blocked_dates[thursday_before] and
                    provider not in leave_blocked_dates[sunday_after]):
                    providers_both_days.append(provider)
    
            # Enforce same provider constraint
            for provider in providers_both_days:
                takes_thursday = shift_vars[provider][thursday_before]['call']
                takes_sunday = shift_vars[provider][sunday_after]['call']
                
                # If provider takes call on Thursday, must take it on Sunday too
                model.Add(takes_sunday == takes_thursday)
    
    # C2. Back-to-back prevention (only for Sunday-Thursday outpatient call)
    
    # Group outpatient dates by week
    outpatient_weeks = defaultdict(list)
    for date in sorted(sun_thu_call_dates):
        week_key = get_schedule_week_key(date)
        outpatient_weeks[week_key].append(date)
    
    # Process each week
    for week_start, week_dates in outpatient_weeks.items():
        # Find holidays in this outpatient week
        week_holidays = [d for d in holiday_dates if d in week_dates]
        
        # Skip back-to-back enforcement if there's a M-Th holiday this week
        # (because holiday pairing rules take precedence)
        mon_to_thu_holiday = any(h.strftime('%A') in ['Monday', 'Tuesday', 'Wednesday', 'Thursday'] 
                                for h in week_holidays)
        if mon_to_thu_holiday:
            continue
        
        # Enforce no back-to-back constraint within the week
        for i in range(len(week_dates) - 1):
            curr_date = week_dates[i]
            next_date = week_dates[i + 1]
            
            # Check if consecutive days
            if (next_date - curr_date).days == 1:
                # Get providers who can take call on both dates
                providers_both_days = []
                for provider in shift_vars:
                    if (curr_date in shift_vars[provider] and 'call' in shift_vars[provider][curr_date] and
                        next_date in shift_vars[provider] and 'call' in shift_vars[provider][next_date]):
                        providers_both_days.append(provider)
                
                # No back-to-back calls
                for provider in providers_both_days:
                    takes_curr = shift_vars[provider][curr_date]['call']
                    takes_next = shift_vars[provider][next_date]['call']
                    model.Add(takes_curr + takes_next <= 1)

    # ========================================================================
    # SECTION D: SOFT CONSTRAINTS (PENALTIES)
    # ========================================================================
    
    # D1. Multiple call penalty for Sunday-Thursday call
    # Penalty for same provider having call twice in a "call week" (Sunday-Thursday)
    
    # Group Sunday-Thursday call dates into "call weeks" 
    call_weeks = defaultdict(list)
    for date in sorted(sun_thu_call_dates):
        week_key = get_schedule_week_key(date)
        call_weeks[week_key].append(date)
    
    # Process each "call week" (Sunday-Thursday)
    for week_start, week_dates in call_weeks.items():
        # Find holidays in this call week
        call_week_holidays = [d for d in holiday_dates if d in week_dates]
        
        # Get all providers who could take call this week
        week_providers = set()
        for d in week_dates:
            for provider in shift_vars:
                if provider in shift_vars and d in shift_vars[provider] and 'call' in shift_vars[provider][d]:
                    week_providers.add(provider)
        
        # For each provider, create penalty for multiple call assignments
        for provider in week_providers:
            # Collect valid call variables for this provider in this call week
            provider_call_vars = []
            for d in week_dates:
                if (provider in shift_vars and 
                    d in shift_vars[provider] and 
                    'call' in shift_vars[provider][d]):
                    provider_call_vars.append(shift_vars[provider][d]['call'])
            
            # Skip if fewer than 2 possible call days
            if len(provider_call_vars) < 2:
                continue
            
            # Create penalty for more than one call assignment
            week_key_str = f"{week_start[0]}_{week_start[1]}_{week_start[2]}"
            penalty_var = model.NewIntVar(0, len(provider_call_vars) - 1, 
                                         f"multiple_call_penalty_{provider}_{week_key_str}")
            
            # Sum of calls should be <= 1 + penalty
            model.Add(sum(provider_call_vars) <= 1 + penalty_var)
            
            # Add penalty to objective with appropriate weight
            # No penalty during holiday weeks (since holiday pairing is required)
            if not call_week_holidays:
                objective_terms.append(penalty_var * 100) 

    return objective_terms

def add_monthly_call_limits(model, 
                            shift_vars, 
                            calendar, 
                            provider_config):
    """
    Limits specific providers to a maximum number of calls per rolling 28-day period.
    
    This constraint prevents call clustering by creating overlapping 28-day windows
    throughout the scheduling period. 

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    calendar : dict
        Dictionary from generate_pediatric_calendar() with call sessions.

    provider_config : dict
        Parsed provider-level information from the YAML (e.g., config['providers']).
        Expected to contain 'max_calls_per_month' for providers with call limits.
    """
    # Get all dates that have call sessions
    call_dates = sorted([d for d in calendar.keys() if 'call' in calendar[d]])
    
    if not call_dates:
        return  # No call dates to constrain
    
    # Identify providers with monthly call limits
    limited_providers = [
        provider for provider, config in provider_config.items()
        if config.get('max_calls_per_month') is not None and config.get('max_calls_per_month') > 0
    ]
    
    if not limited_providers:
        return  # No providers have call limits
    
    # For each provider with a call limit
    for provider in limited_providers:
        max_monthly_calls = provider_config[provider]['max_calls_per_month']
        
        # Create a constraint for every possible 28-day rolling window
        windows_created = 0
        for start_date in call_dates:
            end_date = start_date + timedelta(days=27)  # 28 days total (inclusive)
            
            # Only create constraint if the entire window fits within our scheduling period
            if end_date <= max(call_dates):
                # Find all call variables for this provider within the window
                window_call_vars = []
                for call_date in call_dates:
                    if start_date <= call_date <= end_date:
                        if (provider in shift_vars and 
                            call_date in shift_vars[provider] and 
                            'call' in shift_vars[provider][call_date]):
                            window_call_vars.append(shift_vars[provider][call_date]['call'])
                
                # Add constraint: sum of calls in this 28-day window ≤ max_monthly_calls
                if window_call_vars:
                    model.Add(sum(window_call_vars) <= max_monthly_calls)
                    windows_created += 1

def add_post_call_afternoon_constraints(model, 
                                        shift_vars, 
                                        calendar):
    """
    Ensures providers who were on call the previous night do not have afternoon clinic sessions
    the following day.
    
    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model.
    
    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].
    
    calendar : dict
        Dictionary from generate_pediatric_calendar().
        Keys are datetime.date, values are lists of valid sessions.
    """
    # Find all dates with call sessions
    call_dates = sorted([d for d in calendar.keys() if 'call' in calendar[d]])
    
    # For each call date, block afternoon sessions the next day
    for call_date in call_dates:
        next_day = call_date + timedelta(days = 1)
        
        # Skip if next day is not in calendar
        if next_day not in calendar:
            continue
        
        # Skip if next day does not have afternoon session
        if 'afternoon' not in calendar[next_day]:
            continue
        
        # For each provider
        for provider in shift_vars:
            # Skip if provider doesn't have call on the call date
            if call_date not in shift_vars[provider] or 'call' not in shift_vars[provider][call_date]:
                continue
                
            # Skip if provider doesn't have afternoon session on next day
            if next_day not in shift_vars[provider] or 'afternoon' not in shift_vars[provider][next_day]:
                continue
            
            # Create the constraint: if provider takes call, they cannot have afternoon clinic next day
            call_var = shift_vars[provider][call_date]['call']
            afternoon_var = shift_vars[provider][next_day]['afternoon']
            
            # If call_var is 1, afternoon_var must be 0
            model.Add(afternoon_var == 0).OnlyEnforceIf(call_var)

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
        Parsed DataFrame from inpatient.csv with columns ['provider', 'start_date', 'inpatient_type']
    """
    # Initialize objective terms list if not already done elsewhere
    objective_terms = []
    
    # Identify post-inpatient weeks for clinic reduction
    post_inpatient_weeks = defaultdict(set)
    for _, row in inpatient_starts_df.iterrows():
        provider = row['provider']
        inpatient_end = row['start_date'] + timedelta(days=6)  # Monday
        post_week_tuesday = inpatient_end + timedelta(days=1)  # Tuesday
        week_key = get_schedule_week_key(post_week_tuesday)
        post_inpatient_weeks[provider].add(week_key)

    # Group shifts by provider and week
    weekly_shifts = defaultdict(lambda: defaultdict(list))
    for provider in shift_vars:
        for date in shift_vars[provider]:
            # Use the consistent week key definition
            week_key = get_schedule_week_key(date)
            for session in shift_vars[provider][date]:
                # Only count morning and afternoon sessions as clinic sessions
                if session in ['morning', 'afternoon']:
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
            target_min_clinics = max(1, target_max_clinics) # for more flexibility max(1, target_max_clinics - 1) 
            
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
      - RDO can’t be day of call or day after call
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
        Inpatient assignment days. Must have columns ['provider', 'date', 'inpatient_type'].

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

    # Define providers needing RDO 
    providers_needing_rdo = [
        p for p in provider_roles.keys() 
        if provider_config.get(p, {}).get('needs_rdo', True)  # Default to True if not specified
    ]

    # Identify weeks where providers don't get RDOs
    blocked_weeks = defaultdict(set)

    # Block weeks where provider has leave
    for _, row in leave_df.iterrows():
        provider = row['provider']
        date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        week = get_schedule_week_key(date)
        blocked_weeks[provider].add(week)

    # Block weeks where provider has inpatient duty
    for _, row in inpatient_days_df.iterrows():
        provider = row['provider']
        date = row['date'].date() if hasattr(row['date'], 'date') else row['date']
        week = get_schedule_week_key(date)
        blocked_weeks[provider].add(week)

    # Block holiday weeks for MD/DO providers only
    for holiday_date in holiday_dates:
        holiday_week = get_schedule_week_key(holiday_date)
        for provider, role in provider_roles.items():
            if role in ['MD', 'DO']:
                blocked_weeks[provider].add(holiday_week)

    # Organize dates by provider and week, keeping only eligible DAY OF WEEK
    # ONLY for providers who need RDOs
    eligible_dates_by_provider_week = defaultdict(lambda: defaultdict(list))
    for provider in shift_vars:
        # Skip providers who don't need RDOs
        if provider not in providers_needing_rdo:
            continue
            
        for date in shift_vars[provider]:
            day_of_week = date.strftime('%A')
            if day_of_week in eligible_days:
                week = get_schedule_week_key(date)
                eligible_dates_by_provider_week[provider][week].append(date)

    # Initialize a list to collect penalty variables
    rdo_penalty_vars = []

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
                if date in shift_vars[provider]:
                    # Create the RDO indicator
                    is_rdo = model.NewBoolVar(f"{provider}_RDO_{date.isoformat()}")
                    
                    # Get clinic sessions
                    clinic_sessions = [
                        shift_vars[provider][date][session] 
                        for session in shift_vars[provider][date]
                        if session in ['morning', 'afternoon']
                    ]
                    
                    # RDO means no clinic sessions
                    if clinic_sessions:
                        model.Add(sum(clinic_sessions) == 0).OnlyEnforceIf(is_rdo)
                        model.Add(sum(clinic_sessions) > 0).OnlyEnforceIf(is_rdo.Not())
                    
                    # No RDO on call
                    if 'call' in shift_vars[provider][date]:
                        call_var = shift_vars[provider][date]['call']
                        model.Add(call_var == 0).OnlyEnforceIf(is_rdo)

                    # SOFT CONSTRAINT: Penalize RDO after call
                    yesterday = date - timedelta(days=1)
                    if (yesterday in shift_vars[provider] and 
                        'call' in shift_vars[provider][yesterday]):
                        yesterday_call = shift_vars[provider][yesterday]['call']
                        
                        # Create a penalty variable for post-call RDO
                        post_call_rdo = model.NewBoolVar(f"{provider}_post_call_rdo_{date.isoformat()}")
                        
                        # post_call_rdo is 1 when both yesterday_call and is_rdo are 1
                        model.AddBoolAnd([yesterday_call, is_rdo]).OnlyEnforceIf(post_call_rdo)
                        model.AddBoolOr([yesterday_call.Not(), is_rdo.Not()]).OnlyEnforceIf(post_call_rdo.Not())
                        
                        # Add penalty to the collection
                        rdo_penalty_vars.append(post_call_rdo)
                    
                    rdo_indicators.append(is_rdo)
            
            # Require exactly one RDO day per week (if eligible days exist)
            if rdo_indicators:
                model.Add(sum(rdo_indicators) == 1)

    # Return the penalty variables to be added to the objective function
    return rdo_penalty_vars   

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
        # Filter to only include morning and afternoon sessions
        clinic_sessions = [s for s in sessions if s in ['morning', 'afternoon']]
        
        for s in clinic_sessions:
            staff_vars = [
                shift_vars[provider][d][s]
                for provider in shift_vars
                if d in shift_vars[provider] and s in shift_vars[provider][d]
            ]
            if staff_vars:
                model.Add(sum(staff_vars) >= min_staff)
                model.Add(sum(staff_vars) <= max_staff)

