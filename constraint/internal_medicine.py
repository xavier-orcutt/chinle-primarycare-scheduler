from ortools.sat.python import cp_model

def create_shift_variables(model, providers, calendar):
    """
    Create binary decision variables for each (provider, date, session). In other words, defines the "playing field."
    
    Parameters:
    ----------
    model : cp_model.CpModel
        The constraint programming model from OR-Tools.
    
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
