import pandas as pd
import calendar
from datetime import date, timedelta
from collections import defaultdict
import os
from pathlib import Path
import yaml

def format_schedule_as_calendar(schedule_df, 
                                config_path = None,
                                leave_requests_path = None,
                                inpatient_path = None):
    """
    Converts a schedule DataFrame into a calendar format suitable for display.
    
    Parameters:
    ----------
    schedule_df : pd.DataFrame
        Schedule DataFrame with columns ['date', 'day_of_week', 'session', 'providers', 'count'].

    config_path : str
        Path to department YAML config file
    
    leave_requests_path : str
        Path to the leave requests CSV file

    inpatient_path : str
        Path to the inpatient assignments CSV file
    
    Returns:
    -------
    dict
        Calendar data organized by month and week for easy display.
    """    
    if schedule_df.empty:
        print("Warning: DataFrame is empty")
        return {'months': []}
    
    # Ensure date column exists
    if 'date' not in schedule_df.columns:
        raise ValueError(f"Expected 'date' column. Found: {list(schedule_df.columns)}")
    
    # Convert date column to datetime if it's not already
    try:
        if not pd.api.types.is_datetime64_any_dtype(schedule_df['date']):
            print("Converting date column to datetime...")
            schedule_dates = pd.to_datetime(schedule_df['date']).dt.date
        else:
            schedule_dates = pd.to_datetime(schedule_df['date']).dt.date
    except Exception as e:
        print(f"Error converting dates: {e}")
        print(f"Sample date values: {schedule_df['date'].head()}")
        raise
        
    start_date = schedule_dates.min()
    end_date = schedule_dates.max()
    print(f"Calendar range: {schedule_dates.min()} to {schedule_dates.max()}")
    
    # Group schedule data by date and session
    schedule_by_date = defaultdict(lambda: defaultdict(list))
    session_col = 'session'
    
    print(f"Processing {len(schedule_df)} schedule entries...")
    
    for idx, row in schedule_df.iterrows():
        try:
            # Convert date
            if pd.api.types.is_datetime64_any_dtype(schedule_df['date']):
                date_obj = pd.to_datetime(row['date']).date()
            else:
                date_obj = pd.to_datetime(row['date']).date()
                
            session = row[session_col]
            
            # Handle providers column - could be string or already a list
            if 'providers' in row and pd.notna(row['providers']):
                if isinstance(row['providers'], str):
                    providers = [p.strip() for p in row['providers'].split(',') if p.strip()]
                else:
                    providers = [str(row['providers']).strip()] if str(row['providers']).strip() else []
            else:
                providers = []
            
            if providers:  # Only add if there are providers
                schedule_by_date[date_obj][session] = providers
                
        except Exception as e:
            print(f"Error processing row {idx}: {e}")
            print(f"Row data: {row}")
            continue
    
    print(f"Processed schedule data for {len(schedule_by_date)} unique dates")
    
    # Get department providers for leave and inpatient assignments 
    department_providers = None
    if config_path:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        department_providers = list(config['providers'].keys())

    # Process leave requests if provided
    leave_by_date = defaultdict(list)
    if leave_requests_path and department_providers:
        try:
            leave_df = pd.read_csv(Path(leave_requests_path))
            leave_df = leave_df.query('provider in @department_providers')
            print(f"Filtered to {len(leave_df)} leave requests for this department")

            if not leave_df.empty:
                print(f"Processing {len(leave_df)} leave requests...")
                
                for idx, row in leave_df.iterrows():
                    try:
                        # Convert leave date
                        if pd.api.types.is_datetime64_any_dtype(leave_df['date']):
                            leave_date = pd.to_datetime(row['date']).date()
                        else:
                            leave_date = pd.to_datetime(row['date']).date()
                        
                        provider = row['provider'].strip()
                        leave_by_date[leave_date].append(provider)
                        
                    except Exception as e:
                        print(f"Error processing leave row {idx}: {e}")
                        continue
                
                print(f"Processed leave requests for {len(leave_by_date)} unique dates")
        except Exception as e:
            print(f"Error processing leave requests: {e}")
    
    # Process inpatient assignments if provided
    inpatient_by_date = defaultdict(list)
    if inpatient_path and department_providers:
        try:
            inpatient_df = pd.read_csv(Path(inpatient_path))
            inpatient_df = inpatient_df.query('provider in @department_providers')
            print(f"Filtered to {len(inpatient_df)} inpatient assignments for this department")

            if not inpatient_df.empty:
                print(f"Processing {len(inpatient_df)} inpatient assignments...")
                
                for idx, row in inpatient_df.iterrows():
                    try:
                        # Convert start date
                        if pd.api.types.is_datetime64_any_dtype(inpatient_df['start_date']):
                            start_date_obj = pd.to_datetime(row['start_date']).date()
                        else:
                            start_date_obj = pd.to_datetime(row['start_date']).date()
                        
                        provider = row['provider'].strip()
                        
                        # Add provider to all 7 days of inpatient week
                        for day_offset in range(7):
                            inpatient_date = start_date_obj + timedelta(days=day_offset)
                            inpatient_by_date[inpatient_date].append(provider)
                        
                    except Exception as e:
                        print(f"Error processing inpatient row {idx}: {e}")
                        continue
                
                print(f"Processed inpatient assignments for {len(inpatient_by_date)} unique dates")
        except Exception as e:
            print(f"Error processing inpatient assignments: {e}")

    # NEW: Find all weeks that contain scheduled dates
    scheduled_dates = set(schedule_dates)
    scheduled_weeks = set()
    
    for sched_date in scheduled_dates:
        # Find the Sunday that starts this week
        first_weekday = sched_date.weekday()
        week_start = sched_date - timedelta(days=(first_weekday + 1) % 7)
        scheduled_weeks.add(week_start)
    
    print(f"Found {len(scheduled_weeks)} weeks containing scheduled dates")
    
    # Generate calendar structure - only for weeks with scheduled dates
    months = []

    # Group weeks by month for organization
    weeks_by_month = defaultdict(list)
    for week_start in sorted(scheduled_weeks):
        # Determine which month this week primarily belongs to
        # Use the date that's 3 days into the week (Wednesday) as the representative
        week_mid = week_start + timedelta(days=3)
        month_key = (week_mid.year, week_mid.month)
        weeks_by_month[month_key].append(week_start)
    
    # Generate calendar for each month that has scheduled weeks
    for (year, month), week_starts in sorted(weeks_by_month.items()):
        month_name = date(year, month, 1).strftime('%B %Y')
        
        weeks = []
        
        for week_start in sorted(week_starts):
            week = []
            week_date = week_start
            
            # Build the full week (7 days)
            for day_num in range(7):
                current_date = week_start + timedelta(days=day_num)
                
                # Determine if this date is in the current month
                is_current_month = current_date.month == month
                
                # Get schedule data for this date
                day_sessions = {}
                if current_date in schedule_by_date:
                    day_sessions = dict(schedule_by_date[current_date])
                
                # Add leave requests for this date
                if current_date in leave_by_date:
                    day_sessions['leave'] = leave_by_date[current_date]

                # Add inpatient assignments for this date
                if current_date in inpatient_by_date:
                    day_sessions['inpatient'] = inpatient_by_date[current_date]
                
                day_data = {
                    'date': current_date,
                    'day': current_date.day,
                    'is_current_month': is_current_month,
                    'sessions': day_sessions
                }
                
                week.append(day_data)
            
            weeks.append(week)
        
        months.append({
            'name': month_name,
            'weeks': weeks
        })
    
    return {'months': months}

def create_html_calendar(schedule_df, 
                         config_path = None,
                         leave_requests_path = None,
                         inpatient_path = None, 
                         title = 'calendar',
                         output_dir = ".../output",
                         filename = 'calendar.html'):
    """
    Create HTML calendar and save it to a designated directory.
    Simplified function for easy use in Jupyter notebooks.
    
    Parameters:
    ----------
    schedule_df : pd.DataFrame
        Schedule DataFrame with columns ['date', 'session'/'sessions', 'providers']

    config_path : str
        Path to department YAML config file
    
    leave_requests_path : str
        Path to the leave requests CSV file

    inpatient_path : str
        Path to the inpatient assignments CSV file
        
    title : str
        Title for the HTML calendar
        
    output_dir : str
        Directory where HTML file should be saved (default: "./calendars")
        
    filename : str, optional
        Custom filename. If None, auto-generates based on date range and title
    
    Returns:
    -------
    str
        Path to the saved HTML file
    """
    # Create output directory if it doesn't exist
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    # Get the schedule date range and limit calendar to that range +/- 1 day
    schedule_dates = pd.to_datetime(schedule_df['date']).dt.date
    schedule_start = schedule_dates.min()
    schedule_end = schedule_dates.max()
    
    # Generate calendar data with limited range
    calendar_data = format_schedule_as_calendar(schedule_df = schedule_df, 
                                                config_path = config_path,
                                                leave_requests_path = leave_requests_path,
                                                inpatient_path = inpatient_path)
    
    if not calendar_data or not calendar_data['months']:
        print("Warning: No calendar data generated")
        return None
    
    # Ensure filename ends with .html
    if not filename.endswith('.html'):
        filename += '.html'
    
    full_path = output_path / filename
    
    # Generate HTML content with improved styling
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 10px;
            background-color: #f8f9fa;
        }}
        .header {{
            text-align: center;
            margin-bottom: 20px;
            padding: 15px;
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .legend {{
            background-color: white;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .legend-items {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 8px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            font-size: 13px;
        }}
        .legend-color {{
            width: 16px;
            height: 16px;
            border-radius: 2px;
            margin-right: 8px;
            border-left: 3px solid;
        }}
        .legend-color.morning {{
            background-color: #e8f4fd;
            border-left-color: #2196f3;
        }}
        .legend-color.afternoon {{
            background-color: #fff8e1;
            border-left-color: #ff9800;
        }}
        .legend-color.call {{
            background-color: #ffebee;
            border-left-color: #f44336;
        }}
        .legend-color.inpatient {{
            background-color: #e8f5e8;
            border-left-color: #4caf50;
        }}
        .legend-color.leave {{
            background-color: #f3e5f5;
            border-left-color: #9c27b0;
        }}
        .month {{
            margin-bottom: 30px;
            background-color: white;
            border-radius: 8px;
            padding: 15px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .month-header {{
            font-size: 24px;
            font-weight: bold;
            text-align: center;
            margin-bottom: 15px;
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 8px;
        }}
        .calendar {{
            width: 100%;
            border-collapse: collapse;
            border: 2px solid #34495e;
            border-radius: 6px;
            overflow: hidden;
        }}
        .calendar th {{
            background-color: #3498db;
            color: white;
            padding: 8px;
            text-align: center;
            font-weight: bold;
            font-size: 12px;
        }}
        .calendar td {{
            border: 1px solid #bdc3c7;
            width: 14.28%;
            height: 120px;
            vertical-align: top;
            padding: 4px;
        }}
        .day-number {{
            font-weight: bold;
            font-size: 14px;
            margin-bottom: 4px;
            color: #2c3e50;
        }}
        .other-month {{
            background-color: #ecf0f1;
            color: #95a5a6;
        }}
        .session {{
            font-size: 9px;
            margin: 1px 0;
            padding: 1px 2px;
            border-radius: 2px;
            line-height: 1.2;
            border-left: 2px solid;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }}
        .morning {{
            background-color: #e8f4fd;
            border-left-color: #2196f3;
            color: #1565c0;
        }}
        .afternoon {{
            background-color: #fff8e1;
            border-left-color: #ff9800;
            color: #e65100;
        }}
        .call {{
            background-color: #ffebee;
            border-left-color: #f44336;
            color: #c62828;
        }}
        .inpatient {{
            background-color: #e8f5e8;
            border-left-color: #4caf50;
            color: #2e7d32;
        }}
        .leave {{
            background-color: #f3e5f5;
            border-left-color: #9c27b0;
            color: #7b1fa2;
        }}
        .weekend {{
            background-color: #f8f9fa;
        }}
        .weekend .day-number {{
            color: #6c757d;
        }}
        @media print {{
            body {{ background-color: white; }}
            .month {{ page-break-after: always; box-shadow: none; }}
            .month:last-child {{ page-break-after: auto; }}
            .header {{ box-shadow: none; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{title}</h1>
        <p>Generated on {date.today().strftime('%B %d, %Y')}</p>
    </div>
"""

    for month in calendar_data['months']:
        html_content += f"""
    <div class="month">
        <div class="month-header">{month['name']}</div>
        <table class="calendar">
            <thead>
                <tr>
                    <th>Sunday</th>
                    <th>Monday</th>
                    <th>Tuesday</th>
                    <th>Wednesday</th>
                    <th>Thursday</th>
                    <th>Friday</th>
                    <th>Saturday</th>
                </tr>
            </thead>
            <tbody>
        """
        
        for week in month['weeks']:
            html_content += "<tr>"
            for day in week:
                # Add weekend styling
                is_weekend = day['date'].weekday() in [5, 6]  # Saturday, Sunday
                css_classes = []
                if not day['is_current_month']:
                    css_classes.append("other-month")
                if is_weekend:
                    css_classes.append("weekend")
                
                css_class = " ".join(css_classes)
                html_content += f'<td class="{css_class}">'
                html_content += f'<div class="day-number">{day["day"]}</div>'
                
                # Only show sessions for dates within the schedule range
                is_scheduled_date = schedule_start <= day['date'] <= schedule_end
                
                # For weekends, add spacing to align IP and CALL
                if is_weekend:
                    html_content += '<div style="height: 24px;"></div>'  # Two empty spaces
                
                # For dates not in the original schedule, don't show sessions
                if not is_scheduled_date and not day['sessions']:
                    # Skip processing sessions for dates not in schedule
                    pass
                else:
                    # Order sessions: AM, PM, IP, CALL, LR
                    session_order = ['morning', 'afternoon', 'inpatient', 'call', 'leave']
                    sessions_to_display = []
                    
                    # Add sessions in preferred order - only if they exist
                    for session_type in session_order:
                        if session_type in day['sessions']:
                            sessions_to_display.append((session_type, day['sessions'][session_type]))
                    
                    # Add any remaining sessions not in the standard order
                    for session_type, providers in day['sessions'].items():
                        if session_type not in session_order:
                            sessions_to_display.append((session_type, providers))
                    
                    for i, (session_type, providers) in enumerate(sessions_to_display):
                        # Add a visual spacer for Thursday PM to align with other days
                        if (day['date'].strftime('%A') == 'Thursday' and 
                            session_type == 'afternoon' and
                            not any(s[0] == 'morning' for s in sessions_to_display)):
                            html_content += '<div style="height: 11px;"></div>'  # Space for missing AM
                        
                        # Show all providers
                        provider_text = ', '.join(providers)
                        html_content += f'<div class="session {session_type}">'
                        
                        if session_type == "morning":
                            html_content += f'<strong>AM:</strong> {provider_text}'
                        elif session_type == "afternoon": 
                            html_content += f'<strong>PM:</strong> {provider_text}'
                        elif session_type == "inpatient":
                            html_content += f'<strong>IP:</strong> {provider_text}'
                        elif session_type == "call":
                            html_content += f'<strong>CALL:</strong> {provider_text}'
                        elif session_type == "leave":
                            html_content += f'<strong>LR:</strong> {provider_text}'
                        else:
                            html_content += f'<strong>{session_type.upper()}:</strong> {provider_text}'
                        html_content += '</div>'
                
                html_content += "</td>"
            html_content += "</tr>"
        
        html_content += """
            </tbody>
        </table>
    </div>
        """
    # Add legend at the bottom
    html_content += """
    <div class="legend">
        <div class="legend-items">
            <div class="legend-item">
                <div class="legend-color morning"></div>
                <span><strong>AM:</strong> Morning Clinic</span>
            </div>
            <div class="legend-item">
                <div class="legend-color afternoon"></div>
                <span><strong>PM:</strong> Afternoon Clinic</span>
            </div>
            <div class="legend-item">
                <div class="legend-color inpatient"></div>
                <span><strong>IP:</strong> Inpatient Provider</span>
            </div>
            <div class="legend-item">
                <div class="legend-color call"></div>
                <span><strong>CALL:</strong> Call Assignment</span>
            </div>
            <div class="legend-item">
                <div class="legend-color leave"></div>
                <span><strong>LR:</strong> Leave Request</span>
            </div>
        </div>
    </div>
    """

    html_content += "</body></html>"

    # Save the file
    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"Calendar saved to: {full_path}")