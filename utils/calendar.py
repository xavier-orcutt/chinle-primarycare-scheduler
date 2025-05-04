from datetime import date, timedelta

def generate_clinic_calendar(start_date, end_date, clinic_rules):
    """
    Generates a dictionary mapping each valid clinic date to its allowed clinic sessions 
    (e.g., 'morning', 'afternoon'), based on clinic rules.

    Parameters:
    ----------
    start_date : datetime.date
        The first date to include in the calendar (inclusive). Must be a datetime.date object, 
        e.g., date(2025, 1, 1).
    
    end_date : datetime.date
        The last date to include in the calendar (inclusive). Must be a datetime.date object, 
        e.g., date(2025, 3, 31).

    clinic_rules : dict
        A dictionary of clinic-level rules parsed from internal_medicine.yml. It should include:
            - 'clinic_days': list of weekdays to allow scheduling (e.g., ["Monday", "Tuesday", ...])
            - 'clinic_sessions': dict mapping weekdays to allowed sessions (e.g., {"Monday": ["morning", "afternoon"]})
            - 'holiday_dates': list of datetime.date objects to skip scheduling (optional)

    Returns:
    -------
    dict
        A dictionary where keys are datetime.date objects and values are lists of valid sessions 
        (e.g., ["morning", "afternoon"]) for that date.
 
    """
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise TypeError("start_date and end_date must be datetime.date objects")
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    try:
        clinic_days = clinic_rules['clinic_days']
        clinic_sessions = clinic_rules['clinic_sessions']
    except KeyError as e:
        raise KeyError(f"Missing required key in clinic_rules: {e}")

    holiday_dates = set(clinic_rules.get('holiday_dates', []))
    calendar = {}

    current = start_date
    while current <= end_date:
        weekday = current.strftime('%A')  # e.g., "Monday"
        if weekday in clinic_days and current not in holiday_dates:
            sessions = clinic_sessions.get(weekday, [])
            if sessions:
                calendar[current] = sessions
        current += timedelta(days = 1)

    return calendar