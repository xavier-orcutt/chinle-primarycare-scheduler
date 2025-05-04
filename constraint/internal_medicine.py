from ortools.sat.python import cp_model
from datetime import timedelta

def create_shift_variables(model, providers, calendar):
    """
    Create binary decision variables for each (provider, date, session). In other words, defines the "playing field."
    
    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model
    
    providers : list of str
        List of provider names.
    
    calendar : dict
        Dictionary from generate_clinic_calendar().
        Keys are datetime.date, values are lists of valid sessions (e.g., ['morning', 'afternoon']).

    Returns:
    -------
    dict
        A nested dict: shift_vars[provider][date][session] = BoolVar
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

def add_inpatient_rdo_constraints(model, shift_vars, inpatient_starts_df, clinic_rules):
    """
    For each inpatient assignment, block clinic shifts on the required pre- and post-inpatient RDO days.
    
    Parameters:
    ----------
    model : cp_model.CpModel
        OR-Tools model
    
    shift_vars : dict
        Nested dict of binary decision variables: shift_vars[provider][date][session]
    
    inpatient_starts_df : pd.DataFrame
        DataFrame with columns ['provider', 'start_date'], where start_date is a datetime.date
    
    clinic_rules : dict
        Parsed rules from internal_medicine.yml
    """
    pre_rdo_day = clinic_rules['inpatient_schedule']['pre_inpatient_rdo']  # e.g. 'Monday'
    post_rdo_day = clinic_rules['inpatient_schedule']['post_inpatient_rdo']  # e.g. 'Friday'

    for _, row in inpatient_starts_df.iterrows():
        provider = row['provider']
        start_date = row['start_date']  
        pre_rdo_date = start_date - timedelta(days=1)
        post_rdo_date = start_date + timedelta(days=10)  # next Friday is 10 days from Tuesday

        # Block clinic on pre-inpatient RDO date
        if pre_rdo_date.strftime("%A") == pre_rdo_day:
            for session in shift_vars.get(provider, {}).get(pre_rdo_date, {}):
                model.Add(shift_vars[provider][pre_rdo_date][session] == 0).Name(
                    f"{provider}_pre_inpatient_RDO_{pre_rdo_date}_{session}"
                )

        # Block clinic on post-inpatient RDO date
        if post_rdo_date.strftime("%A") == post_rdo_day:
            for session in shift_vars.get(provider, {}).get(post_rdo_date, {}):
                model.Add(shift_vars[provider][post_rdo_date][session] == 0).Name(
                    f"{provider}_post_inpatient_RDO_{post_rdo_date}_{session}"
                )

# RDO assignment for non-holiday week

# RDO assignmen for holiday week 

# Min/Max clinic staffing 

# Per provider max clinics per week 

# Minimize leave requests 

# For weeks where someone is working less do to granted leave (ie., not-RDO), ensuring distribution 
# of clinic and and admin is preserved 
