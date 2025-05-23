import pandas as pd
import calendar
from datetime import date, timedelta
from collections import defaultdict
import os
from pathlib import Path

def format_schedule_as_calendar(schedule_df, 
                                start_date=None, 
                                end_date=None,
                                department='internal_medicine'):
    """
    Converts a schedule DataFrame into a calendar format suitable for display.
    
    Parameters:
    ----------
    schedule_df : pd.DataFrame
        Schedule DataFrame with columns ['date', 'day_of_week', 'session'/'sessions', 'providers', 'count'].
        For pediatrics, may have 'sessions' instead of 'session'.
    
    start_date : datetime.date, optional
        Start date for calendar. If None, uses min date from schedule_df.
    
    end_date : datetime.date, optional
        End date for calendar. If None, uses max date from schedule_df.
        
    department : str
        Department type ('internal_medicine', 'pediatrics', 'family_practice').
        Affects how sessions are grouped and displayed.
    
    Returns:
    -------
    dict
        Calendar data organized by month and week for easy display.
    """
    # Debug: Print DataFrame info
    if schedule_df.empty:
        print("Warning: DataFrame is empty")
        return {'months': []}
    
    # Handle different column names between departments
    session_col = None
    if 'sessions' in schedule_df.columns:
        session_col = 'sessions'
    elif 'session' in schedule_df.columns:
        session_col = 'session'
    else:
        raise ValueError(f"Expected 'session' or 'sessions' column. Found: {list(schedule_df.columns)}")
    
    # Ensure date column exists
    if 'date' not in schedule_df.columns:
        raise ValueError(f"Expected 'date' column. Found: {list(schedule_df.columns)}")
    
    # Convert date column to datetime if it's not already
    try:
        if not pd.api.types.is_datetime64_any_dtype(schedule_df['date']):
            print("Converting date column to datetime.")
            schedule_dates = pd.to_datetime(schedule_df['date']).dt.date
        else:
            schedule_dates = pd.to_datetime(schedule_df['date']).dt.date
    except Exception as e:
        print(f"Error converting dates: {e}")
        print(f"Sample date values: {schedule_df['date'].head()}")
        raise
        
    print(f"Date range: {schedule_dates.min()} to {schedule_dates.max()}")
    
    # Determine date range
    if start_date is None:
        start_date = schedule_dates.min()
    if end_date is None:
        end_date = schedule_dates.max()
    
    print(f"Calendar range: {start_date} to {end_date}")
    
    # Group schedule data by date and session
    schedule_by_date = defaultdict(lambda: defaultdict(list))
    
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

    # Generate calendar structure
    months = []
    current_date = date(start_date.year, start_date.month, 1)
    
    while current_date <= end_date:
        month_name = current_date.strftime('%B %Y')
        
        # Get first day of month and calculate padding
        first_day = current_date
        last_day = date(current_date.year, current_date.month, 
                       calendar.monthrange(current_date.year, current_date.month)[1])
        
        # Start week on Sunday (0 = Monday, 6 = Sunday in Python)
        first_weekday = first_day.weekday()
        week_start = first_day - timedelta(days=(first_weekday + 1) % 7)
        
        weeks = []
        week_date = week_start
        
        while week_date <= last_day or len(weeks) == 0 or len(weeks[-1]) < 7:
            if len(weeks) == 0 or len(weeks[-1]) == 7:
                weeks.append([])
            
            # Determine if this date is in the current month
            is_current_month = week_date.month == current_date.month
            
            # Get schedule data for this date
            day_sessions = {}
            if week_date in schedule_by_date:
                day_sessions = dict(schedule_by_date[week_date])
            
            day_data = {
                'date': week_date,
                'day': week_date.day,
                'is_current_month': is_current_month,
                'sessions': day_sessions
            }
            
            weeks[-1].append(day_data)
            week_date += timedelta(days=1)
            
            # Stop if we've filled the month and completed the week
            if week_date > last_day and len(weeks[-1]) == 7:
                break
        
        months.append({
            'name': month_name,
            'weeks': weeks
        })
        
        # Move to next month
        if current_date.month == 12:
            current_date = date(current_date.year + 1, 1, 1)
        else:
            current_date = date(current_date.year, current_date.month + 1, 1)
    
    return {'months': months}

def create_html_calendar(schedule_df, 
                         title = 'Calendar',
                         output_dir = '.../output',
                         filename = None):
    """
    Create HTML calendar and save it to a designated directory.
    Simplified function for easy use in Jupyter notebooks.
    
    Parameters:
    ----------
    schedule_df : pd.DataFrame
        Schedule DataFrame with columns ['date', 'session'/'sessions', 'providers']
    
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
    
    print(f"Creating HTML calendar from DataFrame with {len(schedule_df)} entries...")
    
    # Generate calendar data
    calendar_data = format_schedule_as_calendar(schedule_df)
    
    if not calendar_data or not calendar_data['months']:
        print("Warning: No calendar data generated")
        return None
    
    # Auto-generate filename if not provided
    if filename is None:
        # Get date range for filename
        dates = pd.to_datetime(schedule_df['date']).dt.date
        start_date = dates.min()
        end_date = dates.max()
        
        # Clean title for filename
        clean_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        clean_title = clean_title.replace(' ', '_')
        
        if start_date.year == end_date.year and start_date.month == end_date.month:
            # Single month
            date_str = start_date.strftime('%Y_%m_%B')
        else:
            # Multiple months
            date_str = f"{start_date.strftime('%Y_%m')}_to_{end_date.strftime('%Y_%m')}"
        
        filename = f"{clean_title}_{date_str}.html"
    
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
                
                # Order sessions for better display: morning, afternoon, then call
                session_order = ['morning', 'afternoon', 'call']
                sessions_to_display = []
                
                # Add sessions in preferred order
                for session_type in session_order:
                    if session_type in day['sessions']:
                        sessions_to_display.append((session_type, day['sessions'][session_type]))
                
                # Add any remaining sessions not in the standard order
                for session_type, providers in day['sessions'].items():
                    if session_type not in session_order:
                        sessions_to_display.append((session_type, providers))
                
                for session_type, providers in sessions_to_display:
                    if providers:
                        # Show all providers without truncation
                        provider_text = ', '.join(providers)
                        
                        html_content += f'<div class="session {session_type}">'
                        if session_type == "morning":
                            html_content += f'<strong>AM:</strong> {provider_text}'
                        elif session_type == "afternoon": 
                            html_content += f'<strong>PM:</strong> {provider_text}'
                        elif session_type == "call":
                            html_content += f'<strong>CALL:</strong> {provider_text}'
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
    
    html_content += "</body></html>"

    # Save the file
    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"Calendar saved to: {full_path}")