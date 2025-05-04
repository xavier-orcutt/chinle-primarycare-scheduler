import pandas as pd
import yaml
from pathlib import Path
from datetime import timedelta

# Read YAML file and parse into Python dictionary 
def load_yaml_config(yml_path):
    with open(Path(yml_path), "r") as f:
        config = yaml.safe_load(f)
    return config

# Load a CSV and filter for providers in relevant department
def load_and_filter_csv(csv_path, provider_list):
    df = pd.read_csv(Path(csv_path))
    
    # Convert to date time
    df['date'] = pd.to_datetime(df['date'])
    
    # Filter for department providers 
    df = df[df['provider'].isin(provider_list)].copy()
    return df

# Add desired number of inpatient days
def expand_inpatient(inpatient_df, length):
    expanded = []
    for _, row in inpatient_df.iterrows():
        start = row['date']
        for i in range(length):
            expanded.append({
                'provider': row['provider'],
                'date': start + timedelta(days = i),
                'type': 'inpatient',
            })
    return pd.DataFrame(expanded)

# Main function to parse all inputs
def parse_inputs(yml_path, inpatient_csv_path, leave_request_csv_path):
    config = load_yaml_config(yml_path)
    providers = list(config['providers'].keys())
    
    # Defaults to 7
    inpatient_length = (
        config.get('clinic_rules', {})
              .get('inpatient_schedule', {})
              .get('inpatient_length', 7)
    )

    inpatient_df = load_and_filter_csv(inpatient_csv_path, providers)
    leave_df = load_and_filter_csv(leave_request_csv_path, providers)

    inpatient_expanded = expand_inpatient(inpatient_df, inpatient_length)

    return config, inpatient_expanded, leave_df