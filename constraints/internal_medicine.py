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
    # Pull parameters from YML
    holiday_dates = set(clinic_rules.get('holiday_dates', []))
    eligible_rdo_days = set(clinic_rules.get('random_day_off', {}).get('eligible_days', []))
    provider_roles = {name: info['role'] for name, info in provider_config.items()}
    rdo_preference = {name: info.get('rdo_preference') for name, info in provider_config.items()}

    def get_week_key(d):
        return d.isocalendar()[:2]  # (year, week)

    provider_blocked_weeks = defaultdict(set)

    for _, row in leave_df.iterrows():
        provider_blocked_weeks[row['provider']].add(get_week_key(row['date']))
    for _, row in inpatient_days_df.iterrows():
        provider_blocked_weeks[row['provider']].add(get_week_key(row['date']))
    for holiday in holiday_dates:
        week_key = get_week_key(holiday)
        for provider, role in provider_roles.items():
            if role in ['MD', 'DO']:
                provider_blocked_weeks[provider].add(week_key)

    # Group eligible calendar days by provider/week
    calendar_by_week = defaultdict(lambda: defaultdict(list))
    for provider in shift_vars:
        for d in shift_vars[provider]:
            week_key = get_week_key(d)
            if d.strftime('%A') in eligible_rdo_days:
                calendar_by_week[provider][week_key].append(d)

    for provider, week_dates in calendar_by_week.items():
        rdo_pref = rdo_preference.get(provider)

        for week_key, dates in week_dates.items():
            if week_key in provider_blocked_weeks[provider]:
                continue  # Skip RDO assignment for blocked weeks

            # If preference exists, only consider that day
            if rdo_pref:
                dates = [d for d in dates if d.strftime('%A') == rdo_pref]

            rdo_day_vars = []
            for d in dates:
                if d in shift_vars[provider]:
                    day_sum = sum(shift_vars[provider][d].values())
                    rdo_day_vars.append(day_sum)

            if rdo_day_vars:
                bool_vars = []
                for s in rdo_day_vars:
                    b = model.NewBoolVar(f'{provider}_is_RDO_day_{week_key}_{s}')
                    model.Add(s == 0).OnlyEnforceIf(b)
                    model.Add(s > 0).OnlyEnforceIf(b.Not())
                    bool_vars.append(b)

                model.Add(sum(bool_vars) == 1)

def add_clinic_count_constraints(model,
                                 shift_vars,
                                 provider_config,
                                 calendar,
                                 leave_df,
                                 inpatient_starts_df,
                                 inpatient_days_df,
                                 clinic_rules):
    """
    Enforces weekly clinic session targets per provider, based on max_clinics_per_week. 

    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model. 

    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session].

    provider_config : dict
        Parsed provider-level information from the YAML (eg., config['providers']). 

    calendar : dict
        Dictionary from generate_clinic_calendar().
        Keys are datetime.date, values are lists of valid sessions (e.g., ['morning', 'afternoon']).

    leave_df : pd.DataFrame
        DataFrame with leave requests. Must contain 'provider' and 'date' columns.

    inpatient_starts_df : pd.DataFrame
        Parsed DataFrame from inpatient.csv with columns ['provider', 'start_date'], where start_date is a 
        datetime.date.

    inpatient_days_df : pd.DataFrame
        Parsed DataFrame from inpatient.csv with columns: ['provider', 'date'] where each 
        row is a unique date that the provider works inpatient. 
    
    clinic_rules : dict
        Parsed clinic-level rules from the YAML.

    Notes
    -----
    | Rule                              | Action                                             |
    | --------------------------------- | -------------------------------------------------- |
    | Inpatient Tue-Fri                 | Block shifts                                       |
    | Post-inpatient RDO (e.g., Friday) | Block shifts + reduce clinic count by 2            |
    | Pre-inpatient RDO (e.g., Monday)  | Block shifts                                       |
    | Leave days                        | Skip from clinic assignment                        |
    | Weekly clinic max                 | Enforce per provider, adjusting for post-inpatient |

    """
    post_inpatient_leave_day = clinic_rules['inpatient_schedule']['post_inpatient_leave']  # e.g., Friday
    pre_inpatient_leave_day = clinic_rules['inpatient_schedule']['pre_inpatient_leave']    # e.g., Monday
    post_inpatient_week_keys = defaultdict(set)
    inpatient_dates_by_provider = defaultdict(set)

    # Pre- and post-inpatient leave blocking and post-inpatient week tracking
    for _, row in inpatient_starts_df.iterrows():
        provider = row['provider']
        start_date = row['start_date']

        # Pre-inpatient leave (e.g., Monday before start)
        pre_leave_date = start_date - timedelta(days = 1)
        if pre_leave_date.strftime('%A') == pre_inpatient_leave_day:
            inpatient_dates_by_provider[provider].add(pre_leave_date)

        # Post-inpatient leave (e.g., Friday after end)
        post_leave_date = start_date + timedelta(days = 10)
        if post_leave_date.strftime('%A') == post_inpatient_leave_day:
            inpatient_dates_by_provider[provider].add(post_leave_date)
            week_key = post_leave_date.isocalendar()[:2]
            post_inpatient_week_keys[provider].add(week_key)

    # Block inpatient days: Tue–Fri 
    for _, row in inpatient_days_df.iterrows():
        provider = row['provider']
        d = row['date']
        if d.strftime('%A') in ['Tuesday', 'Wednesday', 'Thursday', 'Friday']:
            inpatient_dates_by_provider[provider].add(d)

    # Group shift_vars by provider and week 
    calendar_by_week = defaultdict(lambda: defaultdict(list))
    for provider in shift_vars:
        for d in shift_vars[provider]:
            week_key = d.isocalendar()[:2]
            for session in shift_vars[provider][d]:
                calendar_by_week[provider][week_key].append((d, session))

    # Leave days per provider
    leave_dates_by_provider = {
        p: set(d.date() for d in leave_df[leave_df['provider'] == p]['date'])
        for p in shift_vars
    } 

    # Apply workload constraints     
    provider_clinic_dic = {name: info['max_clinics_per_week'] for name, info in provider_config.items()}
    for provider, week_assignments in calendar_by_week.items():
        max_weekly = provider_clinic_dic[provider]
        leave_dates = leave_dates_by_provider.get(provider, set())

        for week_key, ds_pairs in week_assignments.items():
            valid_vars = []

            for d, s in ds_pairs:
                if d in inpatient_dates_by_provider[provider]:
                    model.Add(shift_vars[provider][d][s] == 0)
                    continue
                if d in leave_dates:
                    continue
                if d in calendar:
                    valid_vars.append(shift_vars[provider][d][s])

            if not valid_vars:
                continue

            expected = max_weekly
            if week_key in post_inpatient_week_keys[provider]:
                expected = max(0, expected - 2)

            expected = min(expected, len(valid_vars))
            model.Add(sum(valid_vars) == expected)


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
            # Gather all shift variables for this session across providers
            staff_vars = []
            for provider in shift_vars:
                if d in shift_vars[provider] and s in shift_vars[provider][d]:
                    staff_vars.append(shift_vars[provider][d][s])

            if staff_vars:
                model.Add(sum(staff_vars) >= min_staff)
                model.Add(sum(staff_vars) <= max_staff)

