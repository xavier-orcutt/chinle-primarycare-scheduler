import pandas as pd
import yaml
from pathlib import Path
from datetime import timedelta

def load_yaml_config(yml_path):
    """
    Loads the department configuration YAML file and returns a Python dictionary.

    Parameters:
    ----------
    yml_path : str or Path
        Path to the internal_medicine.yml file.

    Returns:
    -------
    dict
        Parsed YAML configuration.
    """
    with open(Path(yml_path), "r") as f:
        config = yaml.safe_load(f)
    return config

def load_leave_requests(leave_requests_csv_path, provider_list):
    """
    Loads and filters the leave_requests.csv file to include only valid providers,
    and ensures dates are parsed correctly.

    Parameters:
    ----------
    leave_requests_csv_path : str or Path
        Path to leave_requests.csv.

    provider_list : list of str
        List of provider names to include.

    Returns:
    -------
    pd.DataFrame
        Filtered leave request data with columns ['provider', 'date', 'rank'].
    """
    df = pd.read_csv(Path(leave_requests_csv_path))
    df['date'] = pd.to_datetime(df['date'])
    df = df[df['provider'].isin(provider_list)].copy()
    return df

def load_inpatient(inpatient_csv_path, provider_list, length):
    """
    Loads and expands inpatient schedule data.

    Parameters:
    ----------
    inpatient_csv_path : str or Path
        Path to CSV file with columns ['provider', 'start_date'].
    provider_list : list
        List of provider names to include.
    length : int
        Number of consecutive inpatient days.

    Returns:
    -------
    Tuple of:
        - inpatient_days_df: DataFrame with all inpatient dates (1 row per day)
        - inpatient_starts_df: DataFrame with original inpatient start dates
    """
    df = pd.read_csv(Path(inpatient_csv_path))
    df['start_date'] = pd.to_datetime(df['start_date']).dt.date

    # Filter to only providers in department
    df = df[df['provider'].isin(provider_list)].copy()

    # Create expanded dataframe of inpatient days
    expanded = []
    for _, row in df.iterrows():
        for i in range(length):
            expanded.append({
                'provider': row['provider'],
                'date': row['start_date'] + timedelta(days=i),
            })

    inpatient_days_df = pd.DataFrame(expanded)
    inpatient_starts_df = df[['provider', 'start_date']]

    return inpatient_days_df, inpatient_starts_df

def parse_inputs(yml_path, leave_request_csv_path, inpatient_csv_path):
    """
    Parses all key input files: the YAML config, leave requests, and inpatient schedules.

    Parameters:
    ----------
    yml_path : str or Path
        Path to yml file.

    leave_request_csv_path : str or Path
        Path to leave_requests.csv.

    inpatient_csv_path : str or Path
        Path to inpatient.csv.

    Returns:
    -------
    tuple
        config : dict
            Parsed YAML configuration.
        leave_df : pd.DataFrame
            Filtered leave request DataFrame.
        inpatient_days_df : pd.DataFrame
            DataFrame with all inpatient dates (1 row per day)
        inpatient_starts_df : pd.DataFrame
            DataFrame with original inpatient start dates
    """
    config = load_yaml_config(yml_path)
    providers = list(config['providers'].keys())
    
    # Defaults to 7
    inpatient_length = (
        config.get('clinic_rules', {})
              .get('inpatient_schedule', {})
              .get('inpatient_length', 7)
    )

    leave_df = load_leave_requests(leave_request_csv_path, providers)
    inpatient_days_df, inpatient_starts_df = load_inpatient(inpatient_csv_path, providers, inpatient_length)

    return config, leave_df, inpatient_days_df, inpatient_starts_df