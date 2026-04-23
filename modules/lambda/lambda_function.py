import json
import os
import boto3
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
ses = boto3.client('ses')

# IST timezone offset
IST = timezone(timedelta(hours=5, minutes=30))

# DynamoDB table names from env vars
TABLE_WFH_REQUESTS  = os.environ.get('TABLE_WFH_REQUESTS',  'WFH_Requests')
TABLE_WFH_USERS     = os.environ.get('TABLE_WFH_USERS',     'wfh-users')
TABLE_WFH_AUDIT_LOG = os.environ.get('TABLE_WFH_AUDIT_LOG', 'WFH_Audit_Log')
TABLE_WFH_SETTINGS  = os.environ.get('TABLE_WFH_SETTINGS',  'WFH-Settings')

def get_ist_now():
    """Get current time in IST"""
    return datetime.now(IST).isoformat()

def get_admin_emails():
    """Fetch all admin user emails from wfh-users table"""
    try:
        table = dynamodb.Table(TABLE_WFH_USERS)
        response = table.scan()
        return [
            user['email'] for user in response.get('Items', [])
            if user.get('email') and user.get('role', '').lower() == 'admin'
        ]
    except Exception as e:
        print(f'Error fetching admin emails: {str(e)}')
        return []

def get_user_name_by_email(email):
    """Extract name from email format: firstname.lastname@comprinno.net -> Firstname Lastname"""
    try:
        name_part = email.split('@')[0]
        name_parts = name_part.split('.')
        formatted_name = ' '.join(part.capitalize() for part in name_parts)
        return formatted_name
    except:
        return email

def get_system_holidays():
    """Helper function to get holidays from settings"""
    try:
        table = dynamodb.Table(TABLE_WFH_SETTINGS)
        response = table.get_item(Key={'setting_id': 'system_config'})
        if 'Item' in response and 'holidays' in response['Item']:
            return response['Item']['holidays']
    except:
        pass
    return []

def migrate_legacy_fields(request_item):
    """Migrate legacy field names to consistent structure for backward compatibility"""
    # ALWAYS ensure consistent fields exist
    if 'actioned_by' not in request_item:
        # Default to member_id if no actioned_by
        request_item['actioned_by'] = request_item.get('member_id', '')
        # Use email as name if actioned_by_name is missing (avoid expensive DB calls)
        request_item['actioned_by_name'] = request_item.get('member_id', '').split('@')[0].replace('.', ' ').title()
        request_item['actioned_at'] = request_item.get('created_at', get_ist_now())
        request_item['action_type'] = 'user_submit'
    
    # Handle old admin submission fields
    if 'admin_submitted_by' in request_item:
        if 'admin_context' not in request_item:
            request_item['admin_context'] = {
                'original_admin': request_item.get('admin_submitted_by', ''),
                'admin_name': request_item.get('admin_submitted_by_name', ''),
                'submission_type': 'on_behalf',
                'submitted_at': request_item.get('admin_submitted_at', request_item.get('created_at', ''))
            }
        
        # Override with admin fields
        request_item['actioned_by'] = request_item['admin_submitted_by']
        request_item['actioned_by_name'] = request_item.get('admin_submitted_by_name', '')
        request_item['actioned_at'] = request_item.get('admin_submitted_at', request_item.get('created_at', ''))
        request_item['action_type'] = 'admin_submit'
    
    # Ensure action_type exists
    if 'action_type' not in request_item:
        if request_item.get('status') == 'Cancelled' and request_item.get('actioned_by') == request_item.get('member_id'):
            request_item['action_type'] = 'user_cancel'
        elif request_item.get('status') in ['Approved', 'Rejected'] and 'actioned_by' in request_item:
            request_item['action_type'] = f"manager_{request_item['status'].lower()}"
        else:
            request_item['action_type'] = 'user_submit'
    
    return request_item

def lambda_handler(event, context):
    # Check if this is an EventBridge trigger for reminders
    if event.get('source') == 'aws.events' or event.get('detail-type') == 'Scheduled Event':
        return check_and_send_reminders(event, context)
    
    # Manual test trigger for reminders
    if event.get('test_reminders'):
        return check_and_send_reminders(event, context)
    
    headers = {
        'Access-Control-Allow-Origin': os.environ.get('CORS_ORIGIN', 'https://wfh.comprinno.net'),
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }
    
    try:
        http_method = event.get('httpMethod', '')
        path = event.get('path', '')
        body = event.get('body', '{}')
        query_params = event.get('queryStringParameters') or {}
        
        print(f"Method: {http_method}, Path: {path}")
        
        request_body = json.loads(body) if body else {}
        
        if http_method == 'OPTIONS':
            return {'statusCode': 200, 'headers': headers}

        # Extract caller identity from Cognito token claims (set by API Gateway Authorizer)
        claims = event.get('requestContext', {}).get('authorizer', {}).get('claims', {})
        caller_email = claims.get('email', '')
        caller_role = claims.get('custom:role', '').lower()

        def forbidden():
            return {'statusCode': 403, 'headers': headers, 'body': json.dumps({'error': 'Forbidden'})}

        def is_admin(): return caller_role == 'admin'
        def is_manager_or_admin(): return caller_role in ('manager', 'admin')

        if http_method == 'POST':
            if path == '/submit-request':
                return submit_wfh_request(request_body, headers, caller_email, caller_role)
            elif path == '/employees':
                if not is_admin(): return forbidden()
                return create_employee(request_body, headers)
            elif path == '/settings':
                if not is_admin(): return forbidden()
                return save_settings(request_body, headers)
                
        elif http_method == 'GET':
            if path.startswith('/balance/'):
                member_id = path.split('/')[2]
                if caller_role == 'member' and member_id != caller_email: return forbidden()
                return get_wfh_balance(member_id, headers)
            elif path.startswith('/history/'):
                member_id = path.split('/')[2]
                if caller_role == 'member' and member_id != caller_email: return forbidden()
                return get_request_history(member_id, headers)
            elif path.startswith('/pending/'):
                if not is_manager_or_admin(): return forbidden()
                manager_id = path.split('/')[2]
                return get_pending_requests(manager_id, headers)
            elif path.startswith('/manager-requests/'):
                if not is_manager_or_admin(): return forbidden()
                manager_id = path.split('/')[2]
                return get_manager_all_requests(manager_id, headers)
            elif path == '/all-requests':
                if not is_admin(): return forbidden()
                return get_all_requests(query_params, headers)
            elif path == '/reports':
                if not is_admin(): return forbidden()
                return generate_reports(query_params, headers)
            elif path == '/employees':
                if not is_manager_or_admin(): return forbidden()
                return get_all_employees(query_params, headers)
            elif path.startswith('/calendar/'):
                if not is_manager_or_admin(): return forbidden()
                manager_id = path.split('/')[2]
                return get_team_calendar(manager_id, query_params, headers)
            elif path == '/settings':
                return get_settings(headers)
                
        elif http_method == 'PUT':
            if path == '/approve-reject':
                return approve_reject_request(request_body, headers, caller_email, caller_role)
            elif path == '/update-request':
                return update_wfh_request(request_body, headers, caller_email, caller_role)
            elif path.startswith('/employees/'):
                if not is_admin(): return forbidden()
                user_id = path.split('/')[2]
                return update_employee(user_id, request_body, headers)
                
        elif http_method == 'DELETE':
            if path.startswith('/employees/'):
                if not is_admin(): return forbidden()
                user_id = path.split('/')[2]
                return delete_employee(user_id, headers)
        
        return {
            'statusCode': 404,
            'headers': headers,
            'body': json.dumps({'error': 'Endpoint not found'})
        }
        
    except Exception as e:
        print(f'Error: {str(e)}')
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': 'Internal server error'})
        }

def submit_wfh_request(request_data, headers, caller_email='', caller_role=''):
    print(f"DEBUG: submit_wfh_request called with data: {request_data}")
    
    required_fields = ['member_id', 'request_type', 'from_date', 'to_date', 'location', 'reason', 'work_plan']
    
    for field in required_fields:
        if not request_data.get(field):
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': f'Missing required field: {field}'})
            }
    
    member_id = request_data['member_id']
    # Admin bypass determined by verified token role, not request body flag
    skip_validation = (caller_role == 'admin')
    
    print(f"DEBUG: member_id={member_id}, skip_validation={skip_validation}")
    
    # Get member details from SkillSphere by email
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    try:
        # First try to get by user_id (backward compatibility)
        member_response = skillsphere_users_table.get_item(Key={'user_id': member_id})
        
        # If not found by user_id, scan by email
        if 'Item' not in member_response:
            scan_response = skillsphere_users_table.scan(
                FilterExpression='email = :email',
                ExpressionAttributeValues={':email': member_id}
            )
            if scan_response['Items']:
                member = scan_response['Items'][0]
            else:
                return {
                    'statusCode': 404,
                    'headers': headers,
                    'body': json.dumps({'error': 'Member not found in SkillSphere'})
                }
        else:
            member = member_response['Item']
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching member from SkillSphere: {str(e)}'})
        }
    
    # Skip validations if admin is submitting on behalf (superpower)
    if not skip_validation:
        # Check for duplicate dates in existing requests
        duplicate_check = check_duplicate_dates(member_id, request_data['from_date'], request_data['to_date'])
        if not duplicate_check['valid']:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': duplicate_check['reason']})
            }
        
        # Validate policy compliance
        validation = validate_policy_compliance(
            member, 
            request_data['from_date'], 
            request_data['to_date'], 
            request_data['request_type']
        )
        
        if not validation['valid']:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': validation['reason']})
            }
    
    # Create request
    request_id = f"WFH_{int(datetime.now().timestamp())}_{member_id}"
    
    # Check if the member is a manager (has people reporting to them)
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    manager_check = skillsphere_users_table.scan(
        FilterExpression='manager = :email',
        ExpressionAttributeValues={':email': member_id}
    )
    is_member_a_manager = len(manager_check['Items']) > 0
    
    # Initialize default values
    status = 'Pending'
    notification_type = 'submitted'
    
    # Determine status and notification type based on submission type
    if skip_validation:
        admin_email = request_data.get('admin_id', 'admin@comprinno.net')
        admin_name = get_user_name_by_email(admin_email)
        
        if is_member_a_manager:
            # If member is a manager, directly approve (no self-approval needed)
            status = 'Approved'
            notification_type = 'admin_approved_manager'
        else:
            # If member is regular employee, keep pending for manager approval
            status = 'Pending'
            notification_type = 'admin_submitted'
    
    request_item = {
        'request_id': request_id,
        'member_id': member_id,
        'request_type': request_data['request_type'],
        'from_date': request_data['from_date'],
        'to_date': request_data['to_date'],
        'location': request_data['location'],
        'reason': request_data['reason'],
        'work_plan': request_data['work_plan'],
        'status': status,
        'created_at': get_ist_now(),
        'updated_at': get_ist_now(),
        'is_member_a_manager': is_member_a_manager,
        # Always add consistent action fields
        'actioned_by': member_id,  # User who created the request
        'actioned_by_name': get_user_name_by_email(member_id),
        'actioned_at': get_ist_now(),
        'action_type': 'user_submit'  # Default action type for user submissions
    }
    
    # Add admin submission metadata if submitted by admin
    if skip_validation:
        # Override action fields for admin submissions
        request_item['actioned_by'] = admin_email
        request_item['actioned_by_name'] = admin_name
        request_item['actioned_at'] = get_ist_now()
        request_item['action_type'] = 'admin_submit'
        
        if is_member_a_manager and status == 'Approved':
            # Manager requests get auto-approved
            request_item['manager_comments'] = f'Auto-approved by Admin ({admin_name}) - Manager request'
        else:
            # Employee requests need manager approval
            request_item['manager_comments'] = f'Submitted by Admin ({admin_name}) on behalf of employee'
        
        # Admin context metadata
        request_item['submitted_by_admin'] = True
        request_item['admin_context'] = {
            'original_admin': admin_email,
            'admin_name': admin_name,
            'submission_type': 'on_behalf',
            'submitted_at': get_ist_now()
        }
        request_item['is_member_a_manager'] = is_member_a_manager
    
    # Add reminder tracking fields
    request_item['reminder_count'] = 0
    
    # Set next_reminder_at only if status is Pending (avoid storing None in DynamoDB)
    if request_item.get('status') == 'Pending':
        next_reminder = datetime.now(IST) + timedelta(hours=24)
        request_item['next_reminder_at'] = next_reminder.isoformat()
    
    # Save request
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    requests_table.put_item(Item=request_item)
    
    # Remove auto-approval logic for admin requests - they now go to pending like normal requests
    
    # Send email notifications
    send_wfh_notification_email(request_item, notification_type)
    
    # Update balance
    holidays = get_system_holidays()
    working_days = calculate_working_days(request_data['from_date'], request_data['to_date'], holidays)
    update_wfh_balance(member_id, working_days)
    
    # Log action
    log_action(member_id, 'SUBMIT_REQUEST', {'request_id': request_id})
    
    return {
        'statusCode': 200,
        'headers': headers,
        'body': json.dumps({
            'message': 'Request submitted successfully',
            'request_id': request_id
        })
    }

def update_wfh_request(request_data, headers, caller_email='', caller_role=''):
    required_fields = ['request_id', 'member_id', 'request_type', 'from_date', 'to_date', 'location', 'reason', 'work_plan']
    
    for field in required_fields:
        if not request_data.get(field):
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': f'Missing required field: {field}'})
            }
    
    request_id = request_data['request_id']
    member_id = request_data['member_id']
    
    # Get existing request
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    try:
        response = requests_table.get_item(Key={'request_id': request_id})
        if 'Item' not in response:
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({'error': 'Request not found'})
            }
        
        existing_request = response['Item']
        
        # Check if request belongs to the user (skip for admin updates)
        if existing_request['member_id'] != member_id:
            # Allow admin to edit any request by checking if status is being updated
            if 'status' not in request_data:
                return {
                    'statusCode': 403,
                    'headers': headers,
                    'body': json.dumps({'error': 'Unauthorized to edit this request'})
                }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching request: {str(e)}'})
        }
    
    # Get member details
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    try:
        member_response = skillsphere_users_table.get_item(Key={'user_id': member_id})
        if 'Item' not in member_response:
            scan_response = skillsphere_users_table.scan(
                FilterExpression='email = :email',
                ExpressionAttributeValues={':email': member_id}
            )
            if scan_response['Items']:
                member = scan_response['Items'][0]
            else:
                return {
                    'statusCode': 404,
                    'headers': headers,
                    'body': json.dumps({'error': 'Member not found'})
                }
        else:
            member = member_response['Item']
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching member: {str(e)}'})
        }
    
    # Check for duplicate dates (excluding current request)
    duplicate_check = check_duplicate_dates(member_id, request_data['from_date'], request_data['to_date'], exclude_request_id=request_id)
    if not duplicate_check['valid']:
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({'error': duplicate_check['reason']})
        }
    
    # Check if admin is changing status
    is_admin_status_change = 'status' in request_data and request_data.get('status') != existing_request.get('status')
    old_status = existing_request.get('status')
    new_status = request_data.get('status', 'Pending')
    
    # Admin approving a request - validate balance
    if is_admin_status_change and new_status == 'Approved':
        holidays = get_system_holidays()
        request_working_days = calculate_working_days(
            request_data['from_date'], 
            request_data['to_date'], 
            holidays
        )
        
        # If approving a Rejected/Cancelled request, check if user has enough balance
        if old_status in ['Rejected', 'Cancelled']:
            # Get current balance including all pending requests
            current_balance = member.get('wfh_balance', 0)
            
            if current_balance < request_working_days:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({
                        'error': f'Insufficient balance. User has {current_balance} days available but request needs {request_working_days} days.'
                    })
                }
            
            # Deduct balance for this approval
            update_wfh_balance(member_id, request_working_days)
            print(f"Admin approved rejected request: Deducted {request_working_days} days")
        
        # If approving a Pending request, balance already deducted, just validate it's not negative
        elif old_status == 'Pending':
            current_balance = member.get('wfh_balance', 0)
            if current_balance < 0:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({
                        'error': f'Cannot approve. User balance is negative: {current_balance} days.'
                    })
                }
    
    # Admin rejecting an Approved request - restore balance
    elif is_admin_status_change and new_status == 'Rejected' and old_status == 'Approved':
        holidays = get_system_holidays()
        request_working_days = calculate_working_days(
            existing_request['from_date'], 
            existing_request['to_date'], 
            holidays
        )
        update_wfh_balance(member_id, -request_working_days)
        print(f"Admin rejected approved request: Restored {request_working_days} days")
    
    # Admin rejecting a Pending request - restore balance
    elif is_admin_status_change and new_status == 'Rejected' and old_status == 'Pending':
        holidays = get_system_holidays()
        request_working_days = calculate_working_days(
            existing_request['from_date'], 
            existing_request['to_date'], 
            holidays
        )
        update_wfh_balance(member_id, -request_working_days)
        print(f"Admin rejected pending request: Restored {request_working_days} days")
    
    # Member/Manager editing their own request (not changing status)
    if not is_admin_status_change:
        # For pending request edits, temporarily add back the original request's days to balance
        # This allows users to edit their pending requests without balance issues
        if existing_request.get('status') == 'Pending':
            # Calculate original request's working days
            holidays = get_system_holidays()
            original_working_days = calculate_working_days(
                existing_request['from_date'], 
                existing_request['to_date'], 
                holidays
            )
            
            # Temporarily restore these days to the member's balance
            update_wfh_balance(member_id, -original_working_days)
            
            print(f"Edit validation: Temporarily added back {original_working_days} days from original pending request")
        
        # Validate policy compliance for member updates (skip for admin requests)
        skip_validation = request_data.get('skip_validation', False)
        
        if not skip_validation:
            validation = validate_policy_compliance(
                member, 
                request_data['from_date'], 
                request_data['to_date'], 
                request_data['request_type']
            )
            
            # If validation fails and we restored balance, deduct it back
            if not validation['valid'] and existing_request.get('status') == 'Pending':
                update_wfh_balance(member_id, original_working_days)
                print(f"Edit validation failed: Restored original {original_working_days} days deduction")
            
            if not validation['valid']:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({'error': validation['reason']})
                }
        else:
            print(f"Admin request: Skipping policy compliance validation for request {request_id}")
        
        # If validation passed and request was pending, update balance with the difference
        if existing_request.get('status') == 'Pending':
            new_working_days = calculate_working_days(
                request_data['from_date'], 
                request_data['to_date'], 
                holidays
            )
            balance_difference = new_working_days - original_working_days
            
            # Apply the net difference
            if balance_difference != 0:
                update_wfh_balance(member_id, balance_difference)
                print(f"Edit successful: Applied balance difference of {balance_difference} days")
    
    # Update request
    updated_item = {
        'request_id': request_id,
        'member_id': member_id,
        'request_type': request_data['request_type'],
        'from_date': request_data['from_date'],
        'to_date': request_data['to_date'],
        'location': request_data['location'],
        'reason': request_data['reason'],
        'work_plan': request_data['work_plan'],
        'status': request_data.get('status', 'Pending'),  # Allow status update
        'created_at': existing_request['created_at'],
        'updated_at': get_ist_now()
    }
    
    # Preserve admin context and action metadata if it exists
    preserve_fields = ['submitted_by_admin', 'admin_context', 'is_member_a_manager', 
                      'actioned_by', 'actioned_by_name', 'actioned_at', 'action_type',
                      'reminder_count', 'next_reminder_at', 'last_reminder_sent']
    for field in preserve_fields:
        if field in existing_request:
            updated_item[field] = existing_request[field]

    # On edit, reset reminder schedule to 24 hours from now
    if updated_item.get('status') == 'Pending':
        updated_item['reminder_count'] = 0
        updated_item['next_reminder_at'] = (datetime.now(IST) + timedelta(hours=24)).isoformat()
    
    # If admin is updating status to Approved/Rejected, update action metadata
    if is_admin_status_change and request_data.get('status') in ['Approved', 'Rejected']:
        admin_id = request_data.get('admin_id') or request_data.get('updated_by') or member_id
        updated_item['actioned_by'] = admin_id
        updated_item['actioned_at'] = get_ist_now()
        updated_item['actioned_by_name'] = get_user_name_by_email(admin_id)
        updated_item['action_type'] = 'admin_approve' if request_data.get('status') == 'Approved' else 'admin_reject'
    else:
        # Regular edit by user - update action fields
        updated_item['actioned_by'] = member_id
        updated_item['actioned_at'] = get_ist_now()
        updated_item['actioned_by_name'] = get_user_name_by_email(member_id)
        updated_item['action_type'] = 'user_edit'
    
    # Preserve existing actioned fields if not being updated
    if 'actioned_by' in existing_request and 'actioned_by' not in updated_item:
        updated_item['actioned_by'] = existing_request['actioned_by']
    if 'actioned_at' in existing_request and 'actioned_at' not in updated_item:
        updated_item['actioned_at'] = existing_request['actioned_at']
    
    # Preserve manager_comments if exists
    if 'manager_comments' in existing_request:
        updated_item['manager_comments'] = existing_request['manager_comments']
    
    # Save updated request
    requests_table.put_item(Item=updated_item)
    
    # Send email notification for the edit
    # For admin updates: send notification about admin changes
    # For member/manager updates: send notification about their changes
    send_wfh_edit_notification(updated_item, existing_request, member_id, is_admin_edit=is_admin_status_change)
    
    # Log action
    log_action(member_id, 'UPDATE_REQUEST', {'request_id': request_id})
    
    return {
        'statusCode': 200,
        'headers': headers,
        'body': json.dumps({
            'message': 'Request updated successfully',
            'request_id': request_id
        })
    }

def get_wfh_balance(member_id, headers):
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    
    try:
        # Get member details from SkillSphere by email or user_id
        member_response = skillsphere_users_table.get_item(Key={'user_id': member_id})
        
        # If not found by user_id, scan by email
        if 'Item' not in member_response:
            scan_response = skillsphere_users_table.scan(
                FilterExpression='email = :email',
                ExpressionAttributeValues={':email': member_id}
            )
            if scan_response['Items']:
                member = scan_response['Items'][0]
                actual_user_id = member['user_id']
            else:
                return {
                    'statusCode': 404,
                    'headers': headers,
                    'body': json.dumps({'error': 'Member not found'})
                }
        else:
            member = member_response['Item']
            actual_user_id = member_id
        
        # Calculate entitlement from January 2026 (not from joining date)
        # joining_date is still stored for record keeping but entitlement starts from Jan 2026
        entitlement_start_date = "2026-01-01"
        
        # Check employment status - set entitlement to 0 for restricted statuses
        employee_status = member.get('status', 'Active')
        restricted_statuses = ['Inactive', 'PIP', 'Notice', 'Intern', 'DME']
        
        base_entitlement = 0
        unplanned_deduction = 0
        dme_pip_forfeiture = 0
        
        # Calculate entitlement using monthly status allocation
        # DME/PIP months get 0 days, Active months get 2 days
        allocation_result = calculate_entitlement_with_monthly_status(member_id, member, entitlement_start_date, get_ist_now())
        
        if isinstance(allocation_result, dict):
            base_entitlement = allocation_result['total_entitlement']
            current_month_forfeiture = allocation_result.get('current_month_forfeiture', 0)
            current_month_status = allocation_result.get('current_month_status', 'Active')
        else:
            # Fallback for old return format
            base_entitlement = allocation_result
            current_month_forfeiture = 0
            current_month_status = employee_status
        
        # No separate forfeiture calculation needed - it's built into monthly allocation
        dme_pip_forfeiture = 0
        entitlement_after_forfeiture = base_entitlement
        
        # Apply current status restrictions
        if employee_status in ['Inactive', 'Notice', 'Intern']:
            # Temporary restriction - show 0 but keep calculation for when they become active
            total_entitlement = 0
        elif employee_status in ['DME', 'PIP']:
            # Currently DME/PIP - show 0 (they can't use WFH)
            # The forfeiture calculation already accounts for current month
            total_entitlement = 0
        else:
            # Active status - show entitlement minus any DME/PIP forfeiture periods
            total_entitlement = entitlement_after_forfeiture
            
            # Deduct WFH days for unplanned leaves > 4 (as per policy Section 11)
            unplanned_leaves = int(member.get('unplanned_leaves', 0))
            if unplanned_leaves > 4:
                excess_unplanned = unplanned_leaves - 4
                unplanned_deduction = excess_unplanned
                total_entitlement = max(0, total_entitlement - excess_unplanned)
                print(f"DEBUG: Unplanned leaves adjustment - Total: {unplanned_leaves}, Excess: {excess_unplanned}, Adjusted entitlement: {total_entitlement}")
        
        # Calculate used days from approved requests only
        used_days = 0
        pending_days = 0
        holidays = get_system_holidays()
        try:
            # Get all requests for this member
            response = requests_table.scan(
                FilterExpression='member_id = :member_id',
                ExpressionAttributeValues={':member_id': member_id}
            )
            
            print(f"DEBUG: Found {len(response['Items'])} total requests for {member_id}")
            
            # Separate approved and pending requests
            for request in response['Items']:
                working_days = calculate_working_days(request['from_date'], request['to_date'], holidays)
                
                if request['status'] in ['Approved', 'approve']:
                    used_days += working_days
                    print(f"DEBUG: Approved request {request['request_id']}: {working_days} days, used total: {used_days}")
                elif request['status'] == 'Pending':
                    pending_days += working_days
                    print(f"DEBUG: Pending request {request['request_id']}: {working_days} days, pending total: {pending_days}")
                
        except Exception as e:
            print(f"Error calculating used/pending days: {str(e)}")
            used_days = 0
            pending_days = 0
        
        print(f"DEBUG: Final calculation for {member_id} - Total: {total_entitlement}, Used: {used_days}, Pending: {pending_days}, Available: {total_entitlement - used_days - pending_days}")
        
        available_balance = total_entitlement - used_days - pending_days
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({
                'total_entitlement': total_entitlement,
                'base_entitlement': base_entitlement,
                'entitlement_after_forfeiture': entitlement_after_forfeiture,
                'unplanned_deduction': unplanned_deduction,
                'dme_pip_forfeiture': dme_pip_forfeiture,
                'current_month_forfeiture': current_month_forfeiture,
                'current_month_status': current_month_status,
                'used_days': used_days,
                'pending_days': pending_days,
                'available_balance': available_balance,
                'employment_status': member.get('status', 'Active'),
                'unplanned_leaves': int(member.get('unplanned_leaves', 0))
            })
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching balance: {str(e)}'})
        }

def get_request_history(member_id, headers):
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    
    try:
        # Query by member_id directly (since requests are stored with email as member_id)
        response = requests_table.query(
            IndexName='member-index',
            KeyConditionExpression='member_id = :member_id',
            ExpressionAttributeValues={':member_id': member_id},
            ScanIndexForward=False
        )
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'requests': response['Items']}, default=str)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching history: {str(e)}'})
        }

def get_pending_requests(manager_id, headers):
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    
    try:
        # Get actual manager user_id if manager_id is email
        actual_manager_id = manager_id
        manager_response = skillsphere_users_table.get_item(Key={'user_id': manager_id})
        
        if 'Item' not in manager_response:
            scan_response = skillsphere_users_table.scan(
                FilterExpression='email = :email',
                ExpressionAttributeValues={':email': manager_id}
            )
            if scan_response['Items']:
                actual_manager_id = scan_response['Items'][0]['user_id']
                manager_email = manager_id
            else:
                return {
                    'statusCode': 404,
                    'headers': headers,
                    'body': json.dumps({'error': 'Manager not found'})
                }
        else:
            manager_email = manager_response['Item'].get('email', manager_id)
        
        # Get all pending requests
        response = requests_table.query(
            IndexName='status-index',
            KeyConditionExpression='#status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'Pending'}
        )
        
        # Filter requests for team members managed by this manager
        team_requests = []
        for request in response['Items']:
            member_email = request.get('member_id')  # This is email format
            if member_email:
                # Get member details by email to check manager
                member_scan = skillsphere_users_table.scan(
                    FilterExpression='email = :email',
                    ExpressionAttributeValues={':email': member_email}
                )
                
                if member_scan['Items']:
                    member = member_scan['Items'][0]
                    member_manager = member.get('manager', '')
                    # Check if this manager manages this member
                    if member_manager == manager_email or member_manager == actual_manager_id:
                        # Add member name to the request
                        request['member_name'] = member.get('name', member.get('email', member_email))
                        team_requests.append(request)
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'requests': team_requests}, default=str)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching pending requests: {str(e)}'})
        }

def get_manager_all_requests(manager_id, headers):
    """Get all requests from employees who currently report to this manager"""
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    
    try:
        # Get manager email and name
        manager_response = skillsphere_users_table.get_item(Key={'user_id': manager_id})
        manager_name = None
        
        if 'Item' not in manager_response:
            scan_response = skillsphere_users_table.scan(
                FilterExpression='email = :email',
                ExpressionAttributeValues={':email': manager_id}
            )
            if scan_response['Items']:
                manager_email = manager_id
                manager_name = scan_response['Items'][0].get('name', manager_id)
            else:
                return {
                    'statusCode': 404,
                    'headers': headers,
                    'body': json.dumps({'error': 'Manager not found'})
                }
        else:
            manager_email = manager_response['Item'].get('email', manager_id)
            manager_name = manager_response['Item'].get('name', manager_email)
        
        # Get all employees who currently report to this manager
        team_members_response = skillsphere_users_table.scan(
            FilterExpression='manager = :manager_email',
            ExpressionAttributeValues={':manager_email': manager_email}
        )
        
        team_member_emails = [member['email'] for member in team_members_response['Items']]
        
        # DO NOT add manager's own email - managers should not see/approve their own requests
        # Manager requests should only go to admin for approval
        
        # Get ALL requests from current team members (excluding manager)
        response = requests_table.scan()
        
        team_requests = []
        for request in response['Items']:
            # Migrate legacy fields for backward compatibility
            migrated_request = migrate_legacy_fields(request)
            
            member_email = migrated_request.get('member_id')
            if member_email in team_member_emails and member_email != manager_email:
                # Team member's request (not manager's own)
                member_data = next((m for m in team_members_response['Items'] if m['email'] == member_email), None)
                if member_data:
                    migrated_request['member_name'] = member_data.get('name', member_email)
                else:
                    migrated_request['member_name'] = member_email
                
                team_requests.append(migrated_request)
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'requests': team_requests}, default=str)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching manager requests: {str(e)}'})
        }

def get_request_history(member_id, headers):
    """Get request history for a specific member"""
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    
    try:
        response = requests_table.scan(
            FilterExpression='member_id = :member_id',
            ExpressionAttributeValues={':member_id': member_id}
        )
        
        requests = response['Items']
        
        # Migrate legacy fields and add actioned_by name for each request
        migrated_requests = []
        for request in requests:
            migrated_request = migrate_legacy_fields(request)
            if 'actioned_by' in migrated_request and 'actioned_by_name' not in migrated_request:
                migrated_request['actioned_by_name'] = get_user_name_by_email(migrated_request['actioned_by'])
            migrated_requests.append(migrated_request)
        
        # Sort by created_at (most recent first)
        migrated_requests.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'requests': migrated_requests}, default=str)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching request history: {str(e)}'})
        }

def approve_reject_request(request_data, headers, caller_email='', caller_role=''):
    request_id = request_data.get('request_id')
    action = request_data.get('action')
    manager_id = request_data.get('manager_id')
    comments = request_data.get('comments', '')
    
    if not all([request_id, action, manager_id]):
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({'error': 'Missing required fields'})
        }
    
    # Members can only cancel their own requests
    if caller_role == 'member' and action != 'Cancelled':
        return {'statusCode': 403, 'headers': headers, 'body': json.dumps({'error': 'Forbidden'})}
    
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    
    try:
        # Get request
        request_response = requests_table.get_item(Key={'request_id': request_id})
        if 'Item' not in request_response:
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({'error': 'Request not found'})
            }
        
        request_item = request_response['Item']
        
        # Check if this is an admin-submitted request
        submitted_by_admin = request_item.get('submitted_by_admin', False)
        
        # If approving, check balance ONLY for employee-submitted requests
        holidays = get_system_holidays()
        if action == 'approve' and not submitted_by_admin:
            member_id = request_item['member_id']
            working_days = calculate_working_days(request_item['from_date'], request_item['to_date'], holidays)
            
            # Get current balance
            balance_response = get_wfh_balance(member_id, headers)
            if balance_response['statusCode'] == 200:
                balance_data = json.loads(balance_response['body'])
                available_balance = balance_data.get('available_balance', 0)
                
                # Add back the pending days from THIS request since we're approving it
                # (pending_days already includes this request, so available_balance is already reduced)
                available_balance_for_approval = available_balance + working_days
                
                if working_days > available_balance_for_approval:
                    return {
                        'statusCode': 400,
                        'headers': headers,
                        'body': json.dumps({
                            'error': f'Insufficient WFH balance. Available: {available_balance_for_approval} days, Requested: {working_days} days'
                        })
                    }
        
        # Update request status and clear reminder tracking
        new_status = 'Approved' if action == 'approve' else 'Rejected' if action == 'reject' else action
        action_timestamp = get_ist_now()
        manager_name = get_user_name_by_email(manager_id)
        
        # Determine action type based on who is performing the action
        if new_status == 'Cancelled' and manager_id == request_item['member_id']:
            action_type = 'user_cancel'  # User cancelling their own request
        elif action in ['approve', 'reject']:
            action_type = f'manager_{action}'  # manager_approve, manager_reject
        else:
            action_type = f'manager_{action}'  # fallback
        
        requests_table.update_item(
            Key={'request_id': request_id},
            UpdateExpression='SET #status = :status, manager_comments = :comments, updated_at = :updated_at, actioned_by = :actioned_by, actioned_at = :actioned_at, actioned_by_name = :actioned_by_name, action_type = :action_type REMOVE next_reminder_at',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': new_status,
                ':comments': comments,
                ':updated_at': action_timestamp,
                ':actioned_by': manager_id,
                ':actioned_at': action_timestamp,
                ':actioned_by_name': manager_name,
                ':action_type': action_type
            }
        )
        
        # If rejected, restore balance
        if new_status == 'Rejected':
            days = calculate_days(request_item['from_date'], request_item['to_date'])
            update_wfh_balance(request_item['member_id'], -days)
        
        # Send email notification
        if new_status == 'Approved':
            send_wfh_notification_email(request_item, 'approved')
        elif new_status == 'Rejected':
            send_wfh_notification_email(request_item, 'rejected', rejection_reason=comments)
        
        # Log action
        log_action(manager_id, f'{new_status.upper()}_REQUEST', {'request_id': request_id})
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'message': f'Request {new_status.lower()} successfully'})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error processing request: {str(e)}'})
        }

def get_all_requests(query_params, headers):
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    
    try:
        response = requests_table.scan()
        # Migrate legacy fields for backward compatibility
        migrated_requests = [migrate_legacy_fields(request) for request in response['Items']]
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'requests': migrated_requests}, default=str)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching requests: {str(e)}'})
        }

def generate_reports(query_params, headers):
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    
    try:
        response = requests_table.scan()
        requests = response['Items']
        
        stats = {
            'total_requests': len(requests),
            'pending': len([r for r in requests if r['status'] == 'Pending']),
            'approved': len([r for r in requests if r['status'] in ['Approved', 'approve']]),
            'rejected': len([r for r in requests if r['status'] in ['Rejected', 'reject']])
        }
        
        # Calculate additional analytics
        total_days = 0
        valid_requests = 0
        total_processing_days = 0
        processed_requests = 0
        dept_counts = {}
        
        for request in requests:
            # Calculate average days per request
            if 'from_date' in request and 'to_date' in request:
                try:
                    from_date = datetime.strptime(request['from_date'], '%Y-%m-%d')
                    to_date = datetime.strptime(request['to_date'], '%Y-%m-%d')
                    days = (to_date - from_date).days + 1
                    total_days += days
                    valid_requests += 1
                except:
                    pass
            
            # Calculate processing time
            if ('created_at' in request and 'updated_at' in request and 
                request['status'] != 'Pending'):
                try:
                    created = datetime.fromisoformat(request['created_at'].replace('Z', '+00:00'))
                    updated = datetime.fromisoformat(request['updated_at'].replace('Z', '+00:00'))
                    processing_days = (updated - created).days
                    total_processing_days += processing_days
                    processed_requests += 1
                except:
                    pass
        
        stats['avg_days_per_request'] = round(total_days / valid_requests, 1) if valid_requests > 0 else 0
        stats['avg_processing_days'] = round(total_processing_days / processed_requests, 1) if processed_requests > 0 else 0
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'stats': stats})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error generating reports: {str(e)}'})
        }

def get_all_employees(query_params, headers):
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    
    try:
        response = skillsphere_users_table.scan()
        
        # Transform SkillSphere data to WFH format
        employees = []
        for user in response['Items']:
            employee = {
                'member_id': user.get('user_id', ''),
                'name': user.get('name', ''),
                'email': user.get('email', ''),
                'department': user.get('team', ''),
                'role': user.get('role', 'member'),
                'manager_id': user.get('manager', ''),
                'status': user.get('status', 'Active'),
                'wfh_eligibility': user.get('wfh_eligibility', 'Yes'),
                'joining_date': user.get('joining_date', ''),
                'unplanned_leaves': user.get('unplanned_leaves', 0),
                'created_at': user.get('created_at', ''),
                'updated_at': user.get('updated_at', '')
            }
            employees.append(employee)
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'employees': employees}, default=str)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching employees: {str(e)}'})
        }

def send_wfh_notification_email(request_data, action_type, manager_email=None, rejection_reason=None):
    """
    Send email notifications for WFH requests
    action_type: 'submitted', 'approved', 'rejected'
    """
    try:
        member_email = request_data.get('member_id')  # This is email format
        request_id = request_data.get('request_id')
        request_type = request_data.get('request_type', 'WFH Request')
        from_date = request_data.get('from_date')
        to_date = request_data.get('to_date')
        location = request_data.get('location')
        reason = request_data.get('reason')
        
        # Dashboard URLs
        base_url = "https://wfh.comprinno.net"
        member_url = f"{base_url}/member-dashboard.html"
        manager_url = f"{base_url}/manager-dashboard.html"
        admin_url = f"{base_url}/admin-dashboard.html"
        
        # Check if this is an emergency request
        is_emergency = request_type.lower() == 'emergency' or 'emergency' in reason.lower()
        
        # Get member name from SkillSphere
        skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
        member_scan = skillsphere_users_table.scan(
            FilterExpression='email = :email',
            ExpressionAttributeValues={':email': member_email}
        )
        
        member_name = member_email  # Default fallback
        is_submitter_a_manager = False
        
        if member_scan['Items']:
            member = member_scan['Items'][0]
            member_name = member.get('name', member_email)
            
            # Check if the submitter is a manager (has people reporting to them)
            manager_check = skillsphere_users_table.scan(
                FilterExpression='manager = :email',
                ExpressionAttributeValues={':email': member_email}
            )
            is_submitter_a_manager = len(manager_check['Items']) > 0
            
            if not manager_email:
                manager_email = member.get('manager', '')
        
        # Email templates based on action type
        if action_type == 'submitted':
            # Email to applicant
            applicant_subject = f"{'🚨 URGENT - ' if is_emergency else ''}WFH Request Submitted - {request_id}"
            applicant_body = f"""<html><body>
<p>Dear {member_name},</p>
<p>Your Work From Home request has been successfully submitted and is pending approval.</p>
<p><strong>Request Details:</strong><br>
- Request ID: {request_id}<br>
- Type: {request_type}<br>
- Dates: {from_date} to {to_date}<br>
- Location: {location}<br>
- Reason: {reason}</p>
<p>Your manager will review and respond to your request shortly.</p>
<p>View your requests: {member_url}</p>
<p><a href="{member_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            
            # Email to manager
            manager_subject = f"{'🚨 URGENT - ' if is_emergency else ''}New WFH Request for Approval - {member_name}"
            manager_body = f"""<html><body>
<p>Dear Manager,</p>
{'<p style="color: #ff4444; font-weight: bold; background-color: #ffe6e6; padding: 10px; border-left: 4px solid #ff4444;">⚠️ EMERGENCY REQUEST - IMMEDIATE ATTENTION REQUIRED</p>' if is_emergency else ''}
<p>A new Work From Home request has been submitted by {member_name} and requires your approval.</p>
<p><strong>Request Details:</strong><br>
- Employee: {member_name} ({member_email})<br>
- Request ID: {request_id}<br>
- Type: <strong style="color: {'#ff4444' if is_emergency else 'inherit'};">{request_type}</strong><br>
- Dates: {from_date} to {to_date}<br>
- Location: {location}<br>
- Reason: {reason}</p>
<p>Review and approve/reject: {manager_url}</p>
<p>View your requests: {manager_url}</p>
<p><a href="{manager_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            
            # Email to admin
            admin_subject = f"{'🚨 URGENT - ' if is_emergency else ''}New WFH Request Notification - {member_name}"
            admin_body = f"""<html><body>
<p>Dear Admin,</p>
{'<p style="color: #ff4444; font-weight: bold; background-color: #ffe6e6; padding: 10px; border-left: 4px solid #ff4444;">⚠️ EMERGENCY REQUEST SUBMITTED</p>' if is_emergency else ''}
<p>A new Work From Home request has been submitted in the system.</p>
<p><strong>Request Details:</strong><br>
- Employee: {member_name} ({member_email})<br>
- Manager: {manager_email}<br>
- Request ID: {request_id}<br>
- Type: <strong style="color: {'#ff4444' if is_emergency else 'inherit'};">{request_type}</strong><br>
- Dates: {from_date} to {to_date}<br>
- Location: {location}<br>
- Reason: {reason}</p>
<p>View system overview: {admin_url}</p>
<p>View your requests: {admin_url}</p>
<p><a href="{admin_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            
            # Send emails
            admin_emails = get_admin_emails()
            recipients = [
                (member_email, applicant_subject, applicant_body),
                *[(ae, admin_subject, admin_body) for ae in admin_emails]
            ]
            
            # Only send to manager if:
            # 1. Manager email exists and is different from member
            # 2. The submitter is NOT a manager themselves (managers' requests should only go to admin)
            if manager_email and manager_email != member_email and not is_submitter_a_manager:
                recipients.append((manager_email, manager_subject, manager_body))
        
        elif action_type == 'admin_submitted':
            # Email to applicant - Admin submitted on their behalf
            applicant_subject = f"WFH Request Submitted by Admin - {request_id}"
            applicant_body = f"""<html><body>
<p>Dear {member_name},</p>
<p>A Work From Home request has been submitted on your behalf by an administrator and is pending manager approval.</p>
<p><strong>Request Details:</strong><br>
- Request ID: {request_id}<br>
- Type: {request_type}<br>
- Dates: {from_date} to {to_date}<br>
- Location: {location}<br>
- Reason: {reason}</p>
<p>Your manager will review and respond to this request shortly.</p>
<p>View your requests: {member_url}</p>
<p><a href="{member_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            
            # Email to manager - Admin submitted on employee's behalf
            manager_subject = f"WFH Request Submitted by Admin for {member_name}"
            manager_body = f"""<html><body>
<p>Dear Manager,</p>
<p>A Work From Home request has been submitted by an administrator on behalf of {member_name} and requires your approval.</p>
<p><strong>Request Details:</strong><br>
- Employee: {member_name} ({member_email})<br>
- Request ID: {request_id}<br>
- Type: {request_type}<br>
- Dates: {from_date} to {to_date}<br>
- Location: {location}<br>
- Reason: {reason}<br>
- <strong>Note: This request was submitted by an administrator</strong></p>
<p>Review and approve/reject: {manager_url}</p>
<p><a href="{manager_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            
            # Email to admin - Confirmation of submission
            admin_subject = f"Admin WFH Request Submitted - {member_name}"
            admin_body = f"""<html><body>
<p>Dear Admin,</p>
<p>You have successfully submitted a Work From Home request on behalf of {member_name}.</p>
<p><strong>Request Details:</strong><br>
- Employee: {member_name} ({member_email})<br>
- Manager: {manager_email}<br>
- Request ID: {request_id}<br>
- Type: {request_type}<br>
- Dates: {from_date} to {to_date}<br>
- Location: {location}<br>
- Reason: {reason}</p>
<p>The request is now pending manager approval.</p>
<p>View system overview: {admin_url}</p>
<p><a href="{admin_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            
            admin_emails = get_admin_emails()
            recipients = [(member_email, applicant_subject, applicant_body), (manager_email, manager_subject, manager_body), *[(ae, admin_subject, admin_body) for ae in admin_emails]]
            
        elif action_type == 'admin_approved_manager':
            # Email to manager - Admin directly approved their request
            applicant_subject = f"WFH Request Approved by Admin - {request_id}"
            applicant_body = f"""<html><body>
<p>Dear {member_name},</p>
<p>Your Work From Home request has been approved by an administrator. No further approval is required.</p>
<p><strong>Request Details:</strong><br>
- Request ID: {request_id}<br>
- Type: {request_type}<br>
- Dates: {from_date} to {to_date}<br>
- Location: {location}<br>
- Reason: {reason}</p>
<p>Your WFH request is now confirmed and active.</p>
<p>View your requests: {member_url}</p>
<p><a href="{member_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            
            # Email to admin - Confirmation of direct approval for manager
            admin_subject = f"Manager WFH Request Approved - {member_name}"
            admin_body = f"""<html><body>
<p>Dear Admin,</p>
<p>You have successfully submitted and approved a Work From Home request for {member_name} (Manager).</p>
<p><strong>Request Details:</strong><br>
- Manager: {member_name} ({member_email})<br>
- Request ID: {request_id}<br>
- Type: {request_type}<br>
- Dates: {from_date} to {to_date}<br>
- Location: {location}<br>
- Reason: {reason}</p>
<p>The request has been directly approved as {member_name} is a manager.</p>
<p>View system overview: {admin_url}</p>
<p><a href="{admin_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            
            admin_emails = get_admin_emails()
            recipients = [(member_email, applicant_subject, applicant_body), *[(ae, admin_subject, admin_body) for ae in admin_emails]]
            
        elif action_type == 'approved':
            # Check if this was an admin-submitted request
            was_admin_submitted = request_data.get('submitted_by_admin', False)
            admin_submitted_by_name = request_data.get('admin_submitted_by_name', 'Admin')
            
            if was_admin_submitted:
                subject = f"Admin-Submitted WFH Request Approved - {request_id}"
                body = f"""<html><body style="font-family: Arial, sans-serif; padding: 20px;">
<h2 style="color: #4caf50;">Admin-Submitted WFH Request Approved</h2>
<p>Dear {member_name},</p>
<p>Your Work From Home request (submitted by {admin_submitted_by_name}) has been approved by your manager.</p>
<p><strong>Request Details:</strong></p>
<ul>
    <li>Request ID: {request_id}</li>
    <li>Type: {request_type}</li>
    <li>Dates: {from_date} to {to_date}</li>
    <li>Location: {location}</li>
</ul>
<p>Please ensure you follow all WFH guidelines during your work-from-home period.</p>
<p>View your requests: {member_url}</p>
<p><a href="{member_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            else:
                subject = f"WFH Request Approved - {request_id}"
                body = f"""<html><body style="font-family: Arial, sans-serif; padding: 20px;">
<h2 style="color: #4caf50;">WFH Request Approved</h2>
<p>Dear {member_name},</p>
<p>Your Work From Home request has been approved.</p>
<p><strong>Request Details:</strong></p>
<ul>
    <li>Request ID: {request_id}</li>
    <li>Type: {request_type}</li>
    <li>Dates: {from_date} to {to_date}</li>
    <li>Location: {location}</li>
</ul>
<p>Please ensure you follow all WFH guidelines during your work-from-home period.</p>
<p>View your requests: {member_url}</p>
<p><a href="{member_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            recipients = [(member_email, subject, body)]
        
        elif action_type == 'admin_granted':
            subject = f"WFH Granted by Admin - {request_id}"
            body = f"""<html><body style="font-family: Arial, sans-serif; padding: 20px;">
<h2 style="color: #4caf50;">Work From Home Granted</h2>
<p>Dear {member_name},</p>
<p>Work From Home has been granted to you by the system administrator.</p>
<p><strong>WFH Details:</strong></p>
<ul>
    <li>Reference ID: {request_id}</li>
    <li>Type: {request_type}</li>
    <li>Dates: {from_date} to {to_date}</li>
    <li>Location: {location}</li>
</ul>
<p>This WFH period has been pre-approved. Please ensure you follow all WFH guidelines.</p>
<p>View your requests: {member_url}</p>
<p><a href="{member_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            recipients = [(member_email, subject, body)]
        
        elif action_type == 'rejected':
            subject = f"WFH Request Rejected - {request_id}"
            body = f"""<html><body style="font-family: Arial, sans-serif; padding: 20px;">
<h2 style="color: #f44336;">WFH Request Rejected</h2>
<p>Dear {member_name},</p>
<p>Your Work From Home request has been rejected.</p>
<p><strong>Request Details:</strong></p>
<ul>
    <li>Request ID: {request_id}</li>
    <li>Type: {request_type}</li>
    <li>Dates: {from_date} to {to_date}</li>
    <li>Location: {location}</li>
    <li>Rejection Reason: {rejection_reason or 'Not specified'}</li>
</ul>
<p>Please contact your manager if you need clarification or wish to submit a revised request.</p>
<p>View your requests: {member_url}</p>
<p><a href="{member_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
            recipients = [(member_email, subject, body)]
        
        elif action_type.startswith('reminder_'):
            # Reminder emails for pending requests
            reminder_num = action_type.split('_')[1]
            reminder_text = {
                '1': 'Pending Approval Notice',
                '2': 'Second Follow-up',
                '3': 'Third Follow-up'
            }.get(reminder_num, 'Pending Approval Notice')
            
            # Check if submitter is a manager
            if is_submitter_a_manager:
                # Manager's request - send reminder to Admin only
                admin_subject = f"{reminder_text}: Pending Manager WFH Request - {member_name}"
                admin_body = f"""<html><body>
<h2 style="color: #ff9800;">{reminder_text}: Pending Manager WFH Request</h2>
<p>Dear Admin,</p>
<p>This is a {reminder_text.lower()} that the following manager's WFH request is still pending your approval:</p>
<p><strong>Request Details:</strong></p>
<ul>
    <li>Manager: {member_name} ({member_email})</li>
    <li>Request ID: {request_id}</li>
    <li>Type: {request_type}</li>
    <li>Dates: {from_date} to {to_date}</li>
    <li>Location: {location}</li>
    <li>Reason: {reason}</li>
</ul>
<p><strong>Please review and approve/reject this request at your earliest convenience.</strong></p>
<p>Review request: {admin_url}</p>
<p><a href="{admin_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
                
                # Email to manager (FYI)
                manager_subject = f"{reminder_text}: Your WFH Request is Still Pending"
                manager_body = f"""<html><body>
<h2 style="color: #ff9800;">{reminder_text}: WFH Request Pending Admin Approval</h2>
<p>Dear {member_name},</p>
<p>This is a {reminder_text.lower()} that your WFH request is still pending approval from Admin.</p>
<p><strong>Request Details:</strong></p>
<ul>
    <li>Request ID: {request_id}</li>
    <li>Type: {request_type}</li>
    <li>Dates: {from_date} to {to_date}</li>
    <li>Location: {location}</li>
    <li>Status: Pending admin approval</li>
</ul>
<p>Admin has been notified. You may want to follow up if urgent.</p>
<p>View your requests: {manager_url}</p>
<p><a href="{manager_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
                
                admin_emails = get_admin_emails()
                recipients = [(member_email, manager_subject, manager_body), *[(ae, admin_subject, admin_body) for ae in admin_emails]]
            else:
                # Regular employee's request - send reminder to their manager
                was_admin_submitted = request_data.get('submitted_by_admin', False)
                admin_submitted_note = " (submitted by Admin on behalf of employee)" if was_admin_submitted else ""
                
                manager_subject = f"{reminder_text}: Pending WFH Request - {member_name}{admin_submitted_note}"
                manager_body = f"""<html><body>
<h2 style="color: #ff9800;">{reminder_text}: Pending WFH Approval</h2>
<p>Dear Manager,</p>
<p>This is a {reminder_text.lower()} that the following WFH request is still pending your approval:</p>
{f'<p style="background: #fff3cd; padding: 10px; border-left: 4px solid #ff9800;"><strong>Note:</strong> This request was submitted by Admin on behalf of the employee.</p>' if was_admin_submitted else ''}
<p><strong>Request Details:</strong></p>
<ul>
    <li>Employee: {member_name} ({member_email})</li>
    <li>Request ID: {request_id}</li>
    <li>Type: {request_type}</li>
    <li>Dates: {from_date} to {to_date}</li>
    <li>Location: {location}</li>
    <li>Reason: {reason}</li>
</ul>
<p><strong>Please review and approve/reject this request at your earliest convenience.</strong></p>
<p>Review request: {manager_url}</p>
<p><a href="{manager_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
                
                # Email to admin (FYI)
                admin_subject = f"{reminder_text}: Pending WFH Request - {member_name}"
                admin_body = f"""<html><body>
<h2 style="color: #ff9800;">{reminder_text}: Pending WFH Request</h2>
<p>Dear Admin,</p>
<p>The following WFH request has been pending for an extended period:</p>
{f'<p style="background: #e3f2fd; padding: 10px; border-left: 4px solid #2196f3;"><strong>Note:</strong> You submitted this request on behalf of the employee.</p>' if was_admin_submitted else ''}
<p><strong>Request Details:</strong></p>
<ul>
    <li>Employee: {member_name} ({member_email})</li>
    <li>Manager: {manager_email}</li>
    <li>Request ID: {request_id}</li>
    <li>Type: {request_type}</li>
    <li>Dates: {from_date} to {to_date}</li>
    <li>Reminder: {reminder_text}</li>
</ul>
<p>You may want to follow up with the manager if necessary.</p>
<p>View system overview: {admin_url}</p>
<p><a href="{admin_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
                
                admin_emails = get_admin_emails()
                recipients = [(manager_email, manager_subject, manager_body), *[(ae, admin_subject, admin_body) for ae in admin_emails]]
        
        # Send all emails
        for email, subject, body in recipients:
            if email:  # Only send if email exists
                ses.send_email(
                    Source=os.environ.get('SES_SENDER', 'noreply@skillsphere.comprinno.net'),
                    Destination={'ToAddresses': [email]},
                    Message={
                        'Subject': {'Data': subject},
                        'Body': {'Html': {'Data': body}}
                    }
                )
                print(f"Email sent to {email}: {subject}")
        
        return True
        
    except Exception as e:
        print(f"Error sending email notification: {str(e)}")
        return False

def send_wfh_edit_notification(updated_request, original_request, editor_id, is_admin_edit=False):
    """Send email notifications when a WFH request is edited"""
    try:
        member_email = updated_request.get('member_id')
        request_id = updated_request.get('request_id')
        request_status = updated_request.get('status', 'Pending')
        
        # Dashboard URLs
        base_url = "https://wfh.comprinno.net"
        member_url = f"{base_url}/member-dashboard.html"
        manager_url = f"{base_url}/manager-dashboard.html"
        
        # Get member details
        skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
        member_scan = skillsphere_users_table.scan(
            FilterExpression='email = :email',
            ExpressionAttributeValues={':email': member_email}
        )
        
        member_name = get_user_name_by_email(member_email)
        manager_email = None
        if member_scan['Items']:
            member = member_scan['Items'][0]
            member_name = member.get('name', member_name)
            manager_email = member.get('manager', '')
        
        # Build changes summary
        changes = []
        if original_request.get('from_date') != updated_request.get('from_date'):
            changes.append(f"From Date: {original_request.get('from_date')} → {updated_request.get('from_date')}")
        if original_request.get('to_date') != updated_request.get('to_date'):
            changes.append(f"To Date: {original_request.get('to_date')} → {updated_request.get('to_date')}")
        if original_request.get('location') != updated_request.get('location'):
            changes.append(f"Location: {original_request.get('location')} → {updated_request.get('location')}")
        if original_request.get('reason') != updated_request.get('reason'):
            changes.append(f"Reason: Updated")
        if original_request.get('work_plan') != updated_request.get('work_plan'):
            changes.append(f"Work Plan: Updated")
        if original_request.get('status') != updated_request.get('status'):
            changes.append(f"Status: {original_request.get('status')} → {updated_request.get('status')}")
        
        if not changes:
            return True  # No significant changes to notify
        
        changes_html = "<br>".join([f"• {change}" for change in changes])
        editor_label = "Admin" if is_admin_edit else "You"
        
        # Email to employee
        employee_subject = f"WFH Request Updated{' by Admin' if is_admin_edit else ''} - {request_id}"
        employee_body = f"""<html><body style="font-family: Arial, sans-serif; padding: 20px;">
<h2 style="color: #ff9800;">WFH Request Updated</h2>
<p>Dear {member_name},</p>
<p>Your Work From Home request has been {'updated by Admin' if is_admin_edit else 'successfully updated'}.</p>
<p><strong>Request Details:</strong></p>
<ul>
    <li>Request ID: {request_id}</li>
    <li>Status: <strong style="color: {'#4caf50' if request_status == 'Approved' else '#ff9800' if request_status == 'Pending' else '#f44336'};">{request_status}</strong></li>
    <li>Dates: {updated_request.get('from_date')} to {updated_request.get('to_date')}</li>
    <li>Location: {updated_request.get('location')}</li>
</ul>
<p><strong>Changes Made:</strong></p>
<div style="background-color: #fff3cd; padding: 15px; border-left: 4px solid #ff9800; margin: 15px 0;">
{changes_html}
</div>
<p>Your manager will be notified of these changes.</p>
<p>View your requests: {member_url}</p>
<p><a href="{member_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
        
        # Email to manager
        manager_subject = f"WFH Request Updated{' by Admin' if is_admin_edit else f' by {member_name}'}"
        manager_body = f"""<html><body style="font-family: Arial, sans-serif; padding: 20px;">
<h2 style="color: #ff9800;">{'Admin Updated' if is_admin_edit else 'Team Member Updated'} WFH Request</h2>
<p>Dear Manager,</p>
<p>{'Admin has updated' if is_admin_edit else f'<strong>{member_name}</strong> has updated their'} Work From Home request.</p>
<p><strong>Request Details:</strong></p>
<ul>
    <li>Employee: {member_name} ({member_email})</li>
    <li>Request ID: {request_id}</li>
    <li>Status: <strong>{request_status}</strong></li>
    <li>Updated Dates: {updated_request.get('from_date')} to {updated_request.get('to_date')}</li>
    <li>Location: {updated_request.get('location')}</li>
</ul>
<p><strong>Changes Made:</strong></p>
<div style="background-color: #fff3cd; padding: 15px; border-left: 4px solid #ff9800; margin: 15px 0;">
{changes_html}
</div>
<p>Please review the updated request in your dashboard.</p>
<p>View your requests: {manager_url}</p>
<p><a href="{manager_url}" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
        
        # Email to admin
        admin_subject = f"WFH Request Updated - {member_name}"
        admin_body = f"""<html><body style="font-family: Arial, sans-serif; padding: 20px;">
<h2 style="color: #ff9800;">WFH Request Updated</h2>
<p>Dear Admin,</p>
<p>A Work From Home request has been updated in the system.</p>
<p><strong>Request Details:</strong></p>
<ul>
    <li>Employee: {member_name} ({member_email})</li>
    <li>Manager: {manager_email or 'N/A'}</li>
    <li>Request ID: {request_id}</li>
    <li>Status: <strong>{request_status}</strong></li>
    <li>Updated Dates: {updated_request.get('from_date')} to {updated_request.get('to_date')}</li>
    <li>Location: {updated_request.get('location')}</li>
</ul>
<p><strong>Changes Made:</strong></p>
<div style="background-color: #fff3cd; padding: 15px; border-left: 4px solid #ff9800; margin: 15px 0;">
{changes_html}
</div>
<p>View your requests: {base_url}/admin-dashboard.html</p>
<p><a href="{base_url}/admin-dashboard.html" style="color: black; text-decoration: none;">Best regards,<br>WFH Management System<br>Comprinno Technologies</a></p>
</body></html>"""
        
        # Send emails
        admin_emails = get_admin_emails()
        recipients = [
            (member_email, employee_subject, employee_body),
            *[(ae, admin_subject, admin_body) for ae in admin_emails]
        ]
        
        if manager_email and manager_email != member_email:
            recipients.append((manager_email, manager_subject, manager_body))
        
        for email, subject, body in recipients:
            if email:
                ses.send_email(
                    Source=os.environ.get('SES_SENDER', 'noreply@skillsphere.comprinno.net'),
                    Destination={'ToAddresses': [email]},
                    Message={
                        'Subject': {'Data': subject},
                        'Body': {'Html': {'Data': body}}
                    }
                )
                print(f"Edit notification sent to {email}: {subject}")
        
        return True
        
    except Exception as e:
        print(f"Error sending edit notification: {str(e)}")
        return False

def get_team_calendar(manager_id, query_params, headers):
    """
    Get team WFH calendar data for a manager
    """
    requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    
    try:
        # Get manager's email format
        actual_manager_id = manager_id
        manager_response = skillsphere_users_table.get_item(Key={'user_id': manager_id})
        
        if 'Item' not in manager_response:
            scan_response = skillsphere_users_table.scan(
                FilterExpression='email = :email',
                ExpressionAttributeValues={':email': manager_id}
            )
            if scan_response['Items']:
                actual_manager_id = scan_response['Items'][0]['user_id']
                manager_email = manager_id
            else:
                return {
                    'statusCode': 404,
                    'headers': headers,
                    'body': json.dumps({'error': 'Manager not found'})
                }
        else:
            manager_email = manager_response['Item'].get('email', manager_id)
        
        # Get date range from query params (default to current month)
        from datetime import datetime, timedelta
        today = datetime.now(IST)
        
        # Default to current month if no dates provided
        if not query_params.get('start_date'):
            start_date = today.replace(day=1).strftime('%Y-%m-%d')
        else:
            start_date = query_params.get('start_date')
            
        if not query_params.get('end_date'):
            # Get last day of current month
            next_month = today.replace(day=28) + timedelta(days=4)
            end_date = (next_month - timedelta(days=next_month.day)).strftime('%Y-%m-%d')
        else:
            end_date = query_params.get('end_date')
        
        # Get all approved requests in date range
        response = requests_table.scan()
        all_requests = response['Items']
        
        # Filter for approved requests in date range from manager's team
        calendar_events = []
        for request in all_requests:
            if request.get('status') in ['Approved', 'approve']:
                member_email = request.get('member_id')
                
                # Check if this member reports to this manager
                member_scan = skillsphere_users_table.scan(
                    FilterExpression='email = :email',
                    ExpressionAttributeValues={':email': member_email}
                )
                
                if member_scan['Items']:
                    member = member_scan['Items'][0]
                    member_manager = member.get('manager', '')
                    
                    if member_manager == manager_email or member_manager == actual_manager_id:
                        # Check if request dates overlap with requested range
                        request_start = request.get('from_date')
                        request_end = request.get('to_date')
                        
                        if (request_start and request_end and 
                            request_start <= end_date and request_end >= start_date):
                            calendar_events.append({
                                'id': request.get('request_id'),
                                'employee_name': member.get('name', member_email),
                                'employee_email': member_email,
                                'start_date': request_start,
                                'end_date': request_end,
                                'location': request.get('location'),
                                'type': request.get('request_type'),
                                'reason': request.get('reason')
                            })
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({
                'calendar_events': calendar_events,
                'start_date': start_date,
                'end_date': end_date
            }, default=str)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error fetching calendar: {str(e)}'})
        }

# Helper functions
def calculate_entitlement_by_date(joining_date, target_date):
    """Calculate WFH entitlement up to a specific target date"""
    try:
        # Use January 2026 as the baseline start date for all users
        baseline_start = datetime(2026, 1, 1, tzinfo=IST)
        
        # Parse target date - handle both with and without timezone
        if isinstance(target_date, str):
            if '+' in target_date or 'Z' in target_date:
                target = datetime.fromisoformat(target_date.replace('Z', '+00:00'))
            else:
                target = datetime.fromisoformat(target_date).replace(tzinfo=IST)
        else:
            target = target_date
        
        # Ensure target has timezone info
        if target.tzinfo is None:
            target = target.replace(tzinfo=IST)
        
        # If target date is before January 2026, no entitlement
        if target < baseline_start:
            return 0
        
        # Calculate completed months from January 2026 to target date
        completed_months = 0
        current_month = baseline_start.replace(day=1)
        
        while current_month < target:
            # Move to the end of current month
            if current_month.month == 12:
                next_month = current_month.replace(year=current_month.year + 1, month=1, day=1)
            else:
                next_month = current_month.replace(month=current_month.month + 1, day=1)
            
            # If target date is past the end of this month, count it as completed
            if target >= next_month:
                completed_months += 1
            
            current_month = next_month
            
        return completed_months * 2  # 2 days per completed month
    except Exception as e:
        print(f"Error calculating entitlement by date: {e}")
        return 0

def calculate_days(from_date, to_date):
    from_dt = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
    to_dt = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
    return (to_dt - from_dt).days + 1

def calculate_working_days(from_date, to_date, holidays=None):
    """Calculate working days excluding weekends and mandatory holidays"""
    from_dt = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
    to_dt = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
    
    # Extract mandatory holiday dates from settings
    mandatory_holidays = set()
    if holidays:
        for holiday in holidays:
            # Check if it's a mandatory holiday
            if isinstance(holiday, dict):
                if holiday.get('type') == 'mandatory':
                    mandatory_holidays.add(holiday.get('date'))
            elif isinstance(holiday, str):
                # Legacy format - treat as mandatory
                mandatory_holidays.add(holiday)
    
    working_days = 0
    current_date = from_dt
    
    while current_date <= to_dt:
        # Check if it's a weekday (Monday=0, Sunday=6)
        if current_date.weekday() < 5:  # Monday to Friday
            # Check if it's not a mandatory holiday
            date_str = current_date.strftime('%Y-%m-%d')
            if date_str not in mandatory_holidays:
                working_days += 1
        
        current_date += timedelta(days=1)
    
    return working_days

def check_duplicate_dates(member_id, from_date, to_date, exclude_request_id=None):
    """Check if member has existing requests for the same dates"""
    try:
        # Get all existing requests for this member (excluding rejected and cancelled)
        requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
        response = requests_table.scan(
            FilterExpression='member_id = :member_id AND #status <> :rejected AND #status <> :cancelled',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':member_id': member_id,
                ':rejected': 'Rejected',
                ':cancelled': 'Cancelled'
            }
        )
        
        # Convert request dates to date objects
        request_start = datetime.strptime(from_date, '%Y-%m-%d').date()
        request_end = datetime.strptime(to_date, '%Y-%m-%d').date()
        
        # Generate all dates in the new request range
        request_dates = set()
        current_date = request_start
        while current_date <= request_end:
            request_dates.add(current_date)
            current_date += timedelta(days=1)
        
        # Check for overlaps with existing requests
        for existing_request in response['Items']:
            # Skip the request being edited
            if exclude_request_id and existing_request.get('request_id') == exclude_request_id:
                continue
                
            existing_start = datetime.strptime(existing_request['from_date'], '%Y-%m-%d').date()
            existing_end = datetime.strptime(existing_request['to_date'], '%Y-%m-%d').date()
            
            # Generate all dates in existing request range
            existing_dates = set()
            current_date = existing_start
            while current_date <= existing_end:
                existing_dates.add(current_date)
                current_date += timedelta(days=1)
            
            # Check for overlap
            overlap = request_dates.intersection(existing_dates)
            if overlap:
                overlap_dates = sorted(list(overlap))
                if len(overlap_dates) == 1:
                    date_str = overlap_dates[0].strftime('%Y-%m-%d')
                    return {
                        'valid': False,
                        'reason': f'You already have a WFH request for {date_str}. Please check your existing requests.'
                    }
                else:
                    start_date = overlap_dates[0].strftime('%Y-%m-%d')
                    end_date = overlap_dates[-1].strftime('%Y-%m-%d')
                    return {
                        'valid': False,
                        'reason': f'You already have WFH requests for dates between {start_date} and {end_date}. Please check your existing requests.'
                    }
        
        return {'valid': True}
        
    except Exception as e:
        print(f"Error checking duplicate dates: {str(e)}")
        return {'valid': True}  # Allow request if check fails

def validate_policy_compliance(member, from_date, to_date, request_type):
    # Get dynamic settings
    try:
        settings_table = dynamodb.Table(TABLE_WFH_SETTINGS)
        settings_response = settings_table.get_item(Key={'setting_id': 'system_config'})
        if 'Item' in settings_response:
            settings = settings_response['Item']
            min_advance = int(settings.get('minAdvanceNotice', 15))
            max_advance = int(settings.get('maxAdvanceNotice', 30))
            max_consecutive = int(settings.get('maxConsecutiveDays', 5))
            emergency_hours = int(settings.get('emergencyNotice', 24))
        else:
            # Default values if settings not found
            min_advance = 15
            max_advance = 30
            max_consecutive = 5
            emergency_hours = 24
    except:
        # Default values if error
        min_advance = 15
        max_advance = 30
        max_consecutive = 5
        emergency_hours = 24
    
    # Check if member is active in SkillSphere
    if member.get('status', 'Active') != 'Active':
        return {'valid': False, 'reason': 'WFH not allowed for inactive employees'}
    
    # Check consecutive working days limit (use dynamic setting)
    holidays = get_system_holidays()
    working_days = calculate_working_days(from_date, to_date, holidays)
    if working_days > max_consecutive:
        return {'valid': False, 'reason': f'Maximum {max_consecutive} consecutive working days allowed'}

    # Check consecutive days across existing requests (cross-request streak check)
    try:
        member_email = member.get('email', '')
        requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
        existing = requests_table.scan(
            FilterExpression='member_id = :m AND (#s = :p OR #s = :a)',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':m': member_email, ':p': 'Pending', ':a': 'Approved'}
        )
        # Build set of all WFH working dates (existing + new request)
        mandatory_holidays = {h['date'] for h in holidays if isinstance(h, dict) and h.get('type') == 'mandatory'}
        wfh_dates = set()
        for req in existing['Items']:
            d = datetime.strptime(req['from_date'], '%Y-%m-%d').date()
            end = datetime.strptime(req['to_date'], '%Y-%m-%d').date()
            while d <= end:
                if d.weekday() < 5 and d.strftime('%Y-%m-%d') not in mandatory_holidays:
                    wfh_dates.add(d)
                d += timedelta(days=1)
        # Add new request dates
        d = datetime.strptime(from_date, '%Y-%m-%d').date()
        end = datetime.strptime(to_date, '%Y-%m-%d').date()
        while d <= end:
            if d.weekday() < 5 and d.strftime('%Y-%m-%d') not in mandatory_holidays:
                wfh_dates.add(d)
            d += timedelta(days=1)
        # Find max consecutive streak (weekends + mandatory holidays don't break streak)
        if wfh_dates:
            check_start = min(wfh_dates) - timedelta(days=1)
            check_end = max(wfh_dates) + timedelta(days=1)
            streak = max_streak = 0
            current = check_start
            while current <= check_end:
                is_weekend = current.weekday() >= 5
                is_mandatory_holiday = current.strftime('%Y-%m-%d') in mandatory_holidays
                if is_weekend or is_mandatory_holiday:
                    current += timedelta(days=1)
                    continue
                if current in wfh_dates:
                    streak += 1
                    max_streak = max(max_streak, streak)
                else:
                    streak = 0
                current += timedelta(days=1)
            if max_streak > max_consecutive:
                return {'valid': False, 'reason': f'This request would create {max_streak} consecutive WFH working days. Maximum {max_consecutive} consecutive working days allowed.'}
    except Exception as e:
        print(f'Cross-request consecutive check failed: {str(e)}')  # fail-open
    
    # Check WFH balance based on request date (not current date)
    joining_date = member.get('created_at', get_ist_now())
    entitlement_by_request_date = calculate_entitlement_by_date(joining_date, from_date)
    
    # Calculate used days from ALL approved requests (past and future)
    member_email = member.get('email', '')
    used_days_total = 0
    
    try:
        requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
        response = requests_table.scan(
            FilterExpression='member_id = :member_id AND (#status = :approved1 OR #status = :approved2)',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':member_id': member_email,
                ':approved1': 'Approved',
                ':approved2': 'approve'
            }
        )
        
        # Count only requests that fall within the entitlement period
        request_date = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
        
        for request in response['Items']:
            req_date = datetime.fromisoformat(request['from_date'].replace('Z', '+00:00'))
            # Only count requests up to the month of current request
            if req_date.year < request_date.year or (req_date.year == request_date.year and req_date.month <= request_date.month):
                used_days_total += calculate_working_days(request['from_date'], request['to_date'], holidays)
            
    except Exception as e:
        print(f"Error calculating used days: {str(e)}")
    
    # Check if this request would exceed available balance
    available_balance = entitlement_by_request_date - used_days_total
    if working_days > available_balance:
        return {
            'valid': False, 
            'reason': f'Insufficient WFH balance for {request_date.strftime("%B %Y")}. Available: {available_balance} days, Requested: {working_days} days'
        }
    
    # Check advance notice (use dynamic settings)
    request_submit_date = datetime.now(IST).date()
    wfh_date = datetime.strptime(from_date, '%Y-%m-%d').date()
    days_diff = (wfh_date - request_submit_date).days
    
    print(f"DEBUG: Submit date: {request_submit_date}, WFH date: {wfh_date}, Days diff: {days_diff}")
    
    if request_type == 'Planned' and days_diff < min_advance:
        return {'valid': False, 'reason': f'Planned WFH requires {min_advance} days advance notice. Current: {days_diff} days'}
    
    if request_type == 'Planned' and days_diff > max_advance:
        return {'valid': False, 'reason': f'Planned WFH cannot be requested more than {max_advance} days in advance'}
    
    if request_type == 'Emergency':
        now_ist = datetime.now(IST).replace(tzinfo=None)
        hours_diff = (datetime.combine(wfh_date, datetime.min.time()) - now_ist).total_seconds() / 3600
        if hours_diff < emergency_hours:
            return {'valid': False, 'reason': f'Emergency WFH requires {emergency_hours} hours advance notice'}
    
    return {'valid': True}

def update_wfh_balance(member_id, days):
    # Balance is now calculated in real-time from approved requests
    # No need to maintain separate balance table
    print(f'Balance update skipped for {member_id}: {days} days (using real-time calculation)')

def log_action(member_id, action, details):
    audit_table = dynamodb.Table(TABLE_WFH_AUDIT_LOG)
    
    log_id = f"LOG_{int(datetime.now().timestamp())}_{member_id}"
    audit_table.put_item(Item={
        'log_id': log_id,
        'member_id': member_id,
        'action': action,
        'details': json.dumps(details),
        'timestamp': get_ist_now()
    })


def create_employee(request_body, headers):
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    cognito = boto3.client('cognito-idp')
    
    try:
        name = request_body.get('name')
        email = request_body.get('email', '').lower().strip()
        department = request_body.get('department')
        role = request_body.get('role', 'member').lower().strip()
        manager_email = request_body.get('manager_id', '').lower().strip()
        joining_date = request_body.get('joining_date')  # New field
        
        if not name or not email or not department or not joining_date:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'Name, email, department, and joining date are required'})
            }
        
        # Check for duplicate email in DynamoDB
        existing_check = skillsphere_users_table.scan(
            FilterExpression='email = :email',
            ExpressionAttributeValues={':email': email}
        )
        if existing_check['Items']:
            return {
                'statusCode': 409,
                'headers': headers,
                'body': json.dumps({'error': f'Employee with email {email} already exists'})
            }
        
        # Check for duplicate in Cognito
        try:
            cognito.admin_get_user(
                UserPoolId=os.environ['COGNITO_USER_POOL_ID'],
                Username=email
            )
            return {
                'statusCode': 409,
                'headers': headers,
                'body': json.dumps({'error': f'User with email {email} already exists in Cognito'})
            }
        except cognito.exceptions.UserNotFoundException:
            pass  # Good, user doesn't exist
        
        # Create DynamoDB record
        user_id = f"user_{int(datetime.now().timestamp())}_{email.split('@')[0].replace('.', '_')}"
        timestamp = get_ist_now()
        
        skillsphere_users_table.put_item(Item={
            'user_id': user_id,
            'name': name,
            'email': email,
            'username': email,
            'team': department,
            'role': role,
            'manager': manager_email,
            'status': 'Active',
            'wfh_eligibility': 'Yes',
            'unplanned_leaves': 0,  # Initialize with 0
            'joining_date': joining_date,  # Actual joining date
            'created_at': timestamp,       # Account creation date
            'updated_at': timestamp
        })
        
        # Create Cognito user
        temp_password = str(uuid.uuid4())[:8] + 'Aa1!'
        
        cognito.admin_create_user(
            UserPoolId=os.environ['COGNITO_USER_POOL_ID'],
            Username=email,
            UserAttributes=[
                {'Name': 'email', 'Value': email},
                {'Name': 'email_verified', 'Value': 'true'},
                {'Name': 'name', 'Value': name},
                {'Name': 'custom:team', 'Value': department},
                {'Name': 'custom:role', 'Value': role},
                {'Name': 'custom:manager', 'Value': manager_email}
            ],
            TemporaryPassword=temp_password,
            MessageAction='SUPPRESS'
        )
        
        # Set permanent password (user must change on first login)
        cognito.admin_set_user_password(
            UserPoolId=os.environ['COGNITO_USER_POOL_ID'],
            Username=email,
            Password=temp_password,
            Permanent=False
        )
        
        # Send welcome email with credentials
        ses.send_email(
            Source=os.environ.get('SES_SENDER', 'noreply@skillsphere.comprinno.net'),
            Destination={'ToAddresses': [email]},
            Message={
                'Subject': {'Data': 'Welcome to ComprinnoOne'},
                'Body': {
                    'Html': {
                        'Data': f"""
                        <html>
                        <body style="font-family: Arial, sans-serif; padding: 20px;">
                            <h2 style="color: #040B2A;">Welcome to ComprinnoOne!</h2>
                            <p>Hi {name},</p>
                            <p>Your account has been created successfully. Here are your login credentials:</p>
                            <div style="background: #f0f0f0; padding: 15px; border-radius: 5px; margin: 20px 0;">
                                <p><strong>WFH Management System:</strong> <a href="https://wfh.comprinno.net">Login Here</a></p>
                                <p><strong>SkillSphere - LnD Portal:</strong> <a href="https://skillsphere.comprinno.net/login.html">Login Here</a></p>
                                <p style="margin-top: 15px;"><strong>Email:</strong> {email}</p>
                                <p><strong>Temporary Password:</strong> <code style="background: white; padding: 5px; border: 1px solid #ddd;">{temp_password}</code></p>
                            </div>
                            <p><strong>Important:</strong> You will be required to change your password on first login.</p>
                            <p>If you have any questions, please contact your manager or HR.</p>
                            <hr>
                            <p style="color: #666; font-size: 12px;">This is an automated email from ComprinnoOne.</p>
                        </body>
                        </html>
                        """
                    }
                }
            }
        )
        
        return {
            'statusCode': 201,
            'headers': headers,
            'body': json.dumps({'message': 'Employee created successfully and credentials sent via email', 'user_id': user_id})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error creating employee: {str(e)}'})
        }

def update_employee(user_id, request_body, headers):
    try:
        print(f"DEBUG: update_employee called with user_id: {user_id}")
        print(f"DEBUG: request_body: {request_body}")
        
        # Use global dynamodb resource
        skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
        cognito = boto3.client('cognito-idp')
    except Exception as e:
        print(f"Error initializing services: {str(e)}")
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error initializing services: {str(e)}'})
        }
    
    try:
        # Get current user data
        current_user = skillsphere_users_table.get_item(Key={'user_id': user_id})
        if 'Item' not in current_user:
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({'error': 'Employee not found'})
            }
        
        old_email = current_user['Item']['email']
        old_manager = current_user['Item'].get('manager', '')
        employee_name = current_user['Item'].get('name', 'Employee')
        
        # Update DynamoDB
        update_expr = "SET updated_at = :updated_at"
        expr_values = {':updated_at': get_ist_now()}
        expr_names = {}
        
        # Track if manager is being changed
        manager_changed = False
        new_manager_email = None
        
        if 'name' in request_body:
            update_expr += ", #n = :name"
            expr_values[':name'] = request_body['name']
            expr_names['#n'] = 'name'
        if 'email' in request_body:
            new_email = request_body['email'].lower().strip()
            update_expr += ", email = :email, username = :email"
            expr_values[':email'] = new_email
        if 'department' in request_body:
            update_expr += ", team = :team"
            expr_values[':team'] = request_body['department']
        if 'role' in request_body:
            update_expr += ", #r = :role"
            expr_values[':role'] = request_body['role'].lower().strip()
            expr_names['#r'] = 'role'
        if 'manager_id' in request_body:
            new_manager_email = request_body['manager_id'].lower().strip()
            if new_manager_email != old_manager:
                manager_changed = True
            update_expr += ", manager = :manager"
            expr_values[':manager'] = new_manager_email
        if 'status' in request_body:
            update_expr += ", #s = :status"
            expr_values[':status'] = request_body['status']
            expr_names['#s'] = 'status'
            
            # Automatically set WFH eligibility based on status
            if request_body['status'] == 'Active':
                update_expr += ", wfh_eligibility = :wfh_eligibility"
                expr_values[':wfh_eligibility'] = 'Yes'
            else:  # Inactive, PIP, Notice
                update_expr += ", wfh_eligibility = :wfh_eligibility"
                expr_values[':wfh_eligibility'] = 'No'
        if 'unplanned_leaves' in request_body:
            print(f"DEBUG: Updating unplanned_leaves to {request_body['unplanned_leaves']}")
            update_expr += ", unplanned_leaves = :unplanned_leaves"
            expr_values[':unplanned_leaves'] = int(request_body['unplanned_leaves'])
        if 'joining_date' in request_body:
            print(f"DEBUG: Updating joining_date to {request_body['joining_date']}")
            update_expr += ", joining_date = :joining_date"
            expr_values[':joining_date'] = request_body['joining_date']
        
        print(f"DEBUG: Update expression: {update_expr}")
        print(f"DEBUG: Expression values: {expr_values}")
        print(f"DEBUG: Table object: {skillsphere_users_table}")
        
        try:
            skillsphere_users_table.update_item(
                Key={'user_id': user_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names if expr_names else None,
                ExpressionAttributeValues=expr_values
            )
            print("DEBUG: DynamoDB update successful")
        except Exception as db_error:
            print(f"DEBUG: DynamoDB update failed: {str(db_error)}")
            raise db_error
        
        # Update Cognito user attributes
        try:
            cognito_attrs = []
            if 'name' in request_body:
                cognito_attrs.append({'Name': 'name', 'Value': request_body['name']})
            if 'department' in request_body:
                cognito_attrs.append({'Name': 'custom:team', 'Value': request_body['department']})
            if 'role' in request_body:
                cognito_attrs.append({'Name': 'custom:role', 'Value': request_body['role'].lower().strip()})
            if 'manager_id' in request_body:
                cognito_attrs.append({'Name': 'custom:manager', 'Value': request_body['manager_id']})
            
            if cognito_attrs:
                cognito.admin_update_user_attributes(
                    UserPoolId=os.environ['COGNITO_USER_POOL_ID'],
                    Username=old_email,
                    UserAttributes=cognito_attrs
                )
        except Exception as e:
            print(f"Warning: Could not update Cognito: {str(e)}")
        
        # Send email notifications if manager was changed
        if manager_changed and new_manager_email:
            try:
                send_manager_change_notifications(employee_name, old_email, old_manager, new_manager_email)
            except Exception as e:
                print(f"Warning: Could not send email notifications: {str(e)}")
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'message': 'Employee updated successfully'})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error updating employee: {str(e)}'})
        }

def delete_employee(user_id, headers):
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    cognito = boto3.client('cognito-idp')
    
    try:
        # Get user email before deleting
        user = skillsphere_users_table.get_item(Key={'user_id': user_id})
        if 'Item' not in user:
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({'error': 'Employee not found'})
            }
        
        email = user['Item']['email']
        
        # Delete from DynamoDB
        skillsphere_users_table.delete_item(Key={'user_id': user_id})
        
        # Delete from Cognito
        try:
            cognito.admin_delete_user(
                UserPoolId=os.environ['COGNITO_USER_POOL_ID'],
                Username=email
            )
        except Exception as e:
            print(f"Warning: Could not delete Cognito user: {str(e)}")
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'message': 'Employee deleted successfully from both systems'})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': f'Error deleting employee: {str(e)}'})
        }

def calculate_entitlement_with_monthly_status(member_id, member, start_date, current_date):
    """
    Calculate entitlement based on monthly status allocation
    Restricted statuses (DME, PIP, Inactive, Notice, Intern) get 0 days
    Active status gets 2 days per month
    """
    try:
        from datetime import datetime, timedelta
        
        start = datetime.strptime(start_date, '%Y-%m-%d')
        current = datetime.strptime(current_date.split('T')[0], '%Y-%m-%d') if 'T' in current_date else datetime.strptime(current_date, '%Y-%m-%d')
        
        total_entitlement = 0
        
        # Get monthly status history (if available)
        monthly_status = member.get('monthly_status_history', {})
        current_status = member.get('status', 'Active')
        
        # Iterate through each COMPLETED month from start to current
        current_month = start.replace(day=1)
        
        while current_month < current:
            # Only count months that are fully completed
            # Check if the entire month has passed
            if current_month.month == 12:
                next_month = current_month.replace(year=current_month.year + 1, month=1, day=1)
            else:
                next_month = current_month.replace(month=current_month.month + 1, day=1)
            
            # Only allocate if the entire month has completed
            if current >= next_month:
                month_key = current_month.strftime('%Y-%m')
                
                # Get status for this month (use current status if no history)
                month_status = monthly_status.get(month_key, current_status)
                
                # Allocate days based on status
                if month_status in ['DME', 'PIP', 'Inactive', 'Notice', 'Intern']:
                    month_allocation = 0  # No allocation for restricted statuses
                    print(f"DEBUG: Month {month_key} - Status: {month_status}, Allocation: 0 days (restricted)")
                else:
                    month_allocation = 2  # Standard allocation for Active months
                    print(f"DEBUG: Month {month_key} - Status: {month_status}, Allocation: 2 days")
                
                total_entitlement += month_allocation
            
            # Move to next month
            current_month = next_month
        
        # Handle current month if user is currently in restricted status
        current_month_key = current.strftime('%Y-%m')
        current_month_status = monthly_status.get(current_month_key, current_status)
        
        if current_month_status in ['DME', 'PIP', 'Inactive', 'Notice', 'Intern']:
            # Current month gets 0 allocation due to restricted status
            print(f"DEBUG: Current month {current_month_key} - Status: {current_month_status}, Allocation: 0 days (current restriction)")
            # Don't add to total_entitlement since current month isn't completed
            # But this affects the forfeiture calculation for display purposes
        
        print(f"DEBUG: Monthly allocation calculation - Total entitlement: {total_entitlement} days")
        
        # Calculate current month impact for display (not added to entitlement until month completes)
        current_month_key = current.strftime('%Y-%m')
        current_month_status = monthly_status.get(current_month_key, current_status)
        current_month_forfeiture = 0
        
        if current_month_status in ['DME', 'PIP', 'Inactive', 'Notice', 'Intern']:
            current_month_forfeiture = 2  # Current month will be forfeited
            print(f"DEBUG: Current month {current_month_key} - Status: {current_month_status}, Will forfeit: 2 days")
        
        return {
            'total_entitlement': total_entitlement,
            'current_month_forfeiture': current_month_forfeiture,
            'current_month_status': current_month_status
        }
        
    except Exception as e:
        print(f"Error in monthly allocation calculation: {str(e)}")
        # Fallback to simple calculation
        return calculate_entitlement_by_date(start_date, current_date)
    """
    Calculate lost entitlement due to DME/PIP status
    Policy: DME/PIP employees don't get monthly allocation (2 days/month) during those periods
    """
    try:
        current_status = member.get('status', 'Active')
        
        # If currently DME/PIP, they lose current month's allocation
        if current_status in ['DME', 'PIP']:
            # Current month gets 0 allocation instead of 2 days
            current_month_loss = 2
            print(f"DEBUG: Current DME/PIP status - losing {current_month_loss} days for current month")
        else:
            current_month_loss = 0
        
        # Check for accumulated losses from previous DME/PIP periods
        # This would be tracked in a field like 'dme_pip_months_lost'
        previous_losses = member.get('dme_pip_months_lost', 0) * 2  # months × 2 days each
        
        total_lost_allocation = current_month_loss + previous_losses
        
        print(f"DEBUG: DME/PIP allocation loss - Current month: {current_month_loss}, Previous: {previous_losses}, Total: {total_lost_allocation}")
        
        return total_lost_allocation
        
    except Exception as e:
        print(f"Error calculating DME/PIP allocation loss: {str(e)}")
        return 0

def calculate_affected_months(start_date, end_date=None):
    """Calculate number of months affected by DME/PIP status"""
    try:
        from datetime import datetime
        
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d') if end_date else datetime.now(IST).replace(tzinfo=None)
        
        # Calculate months between start and end
        months = (end.year - start.year) * 12 + (end.month - start.month)
        
        # If we're in the middle of a month, count it as a full month for forfeiture
        if end.day > start.day or not end_date:
            months += 1
            
        return max(0, months)
        
    except Exception as e:
        print(f"Error calculating affected months: {str(e)}")
        return 0
    """Send email notifications when manager is changed by admin"""
    skillsphere_users_table = dynamodb.Table(TABLE_WFH_USERS)
    
    try:
        # Get manager names
        old_manager_name = "Previous Manager"
        new_manager_name = "New Manager"
        
        if old_manager_email:
            try:
                old_manager = skillsphere_users_table.scan(
                    FilterExpression='email = :email',
                    ExpressionAttributeValues={':email': old_manager_email}
                )
                if old_manager['Items']:
                    old_manager_name = old_manager['Items'][0].get('name', old_manager_email)
            except:
                pass
        
        if new_manager_email:
            try:
                new_manager = skillsphere_users_table.scan(
                    FilterExpression='email = :email',
                    ExpressionAttributeValues={':email': new_manager_email}
                )
                if new_manager['Items']:
                    new_manager_name = new_manager['Items'][0].get('name', new_manager_email)
            except:
                pass
        
        # Email to employee
        employee_subject = "Manager Assignment Updated - WFH Management System"
        employee_body = f"""
Dear {employee_name},

Your manager assignment has been updated by the system administrator.

Previous Manager: {old_manager_name} ({old_manager_email if old_manager_email else 'None'})
New Manager: {new_manager_name} ({new_manager_email})

This change is effective immediately. Your new manager will now handle your WFH requests and approvals.

If you have any questions about this change, please contact your HR department.

View your requests: https://wfh.comprinno.net/member-dashboard.html

Best regards,
WFH Management System
Comprinno Technologies
"""
        
        # Email to new manager
        new_manager_subject = "New Team Member Assigned - WFH Management System"
        new_manager_body = f"""
Dear {new_manager_name},

You have been assigned as the manager for {employee_name} ({employee_email}) in the WFH Management System.

Employee Details:
- Name: {employee_name}
- Email: {employee_email}
- Previous Manager: {old_manager_name} ({old_manager_email if old_manager_email else 'None'})

You will now be responsible for:
- Reviewing and approving/rejecting their WFH requests
- Monitoring their WFH usage and compliance
- Managing their team calendar

Please log in to your manager dashboard to view any pending requests.

View your requests: https://wfh.comprinno.net/manager-dashboard.html

Best regards,
WFH Management System
Comprinno Technologies
"""
        
        # Send emails
        recipients = [employee_email]
        if new_manager_email:
            recipients.append(new_manager_email)
        
        for recipient in recipients:
            if recipient == employee_email:
                subject = employee_subject
                body = employee_body
            else:
                subject = new_manager_subject
                body = new_manager_body
            
            ses.send_email(
                Source=os.environ.get('SES_SENDER', 'noreply@skillsphere.comprinno.net'),
                Destination={'ToAddresses': [recipient]},
                Message={
                    'Subject': {'Data': subject},
                    'Body': {'Text': {'Data': body}}
                }
            )
        
        print(f"Manager change notifications sent to: {', '.join(recipients)}")
        
    except Exception as e:
        print(f"Error sending manager change notifications: {str(e)}")
        raise e

def get_settings(headers):
    """Get WFH system settings"""
    try:
        table = dynamodb.Table(TABLE_WFH_SETTINGS)
        
        # Try to get settings, if not found return defaults
        try:
            response = table.get_item(Key={'setting_id': 'system_config'})
            if 'Item' in response:
                settings = response['Item']
                # Convert Decimal to int for JSON serialization
                for key, value in settings.items():
                    if isinstance(value, Decimal):
                        settings[key] = int(value)
                return {
                    'statusCode': 200,
                    'headers': headers,
                    'body': json.dumps(settings)
                }
        except:
            pass
            
        # Return default settings if not found
        default_settings = {
            'setting_id': 'system_config',
            'minAdvanceNotice': 15,
            'maxAdvanceNotice': 30,
            'emergencyNotice': 24,
            'maxConsecutiveDays': 5,
            'annualEntitlement': 24,
            'holidays': []
        }
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(default_settings)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': str(e)})
        }

def save_settings(request_body, headers):
    """Save WFH system settings"""
    try:
        table = dynamodb.Table(TABLE_WFH_SETTINGS)
        
        settings = {
            'setting_id': 'system_config',
            'minAdvanceNotice': int(request_body.get('minAdvanceNotice', 15)),
            'maxAdvanceNotice': int(request_body.get('maxAdvanceNotice', 30)),
            'emergencyNotice': int(request_body.get('emergencyNotice', 24)),
            'maxConsecutiveDays': int(request_body.get('maxConsecutiveDays', 5)),
            'annualEntitlement': int(request_body.get('annualEntitlement', 24)),
            'holidays': request_body.get('holidays', []),
            'updated_at': get_ist_now()
        }
        
        table.put_item(Item=settings)
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'message': 'Settings saved successfully'})
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({'error': str(e)})
        }


def check_and_send_reminders(event, context):
    """
    Check for pending requests that need reminders and send emails.
    Triggered by EventBridge hourly.
    Uses IST for all timestamp comparisons.
    """
    from datetime import datetime, timedelta
    
    try:
        requests_table = dynamodb.Table(TABLE_WFH_REQUESTS)
        current_time = datetime.now(IST)
        
        print(f'Reminder check started at: {current_time.isoformat()}')
        
        # Scan for pending requests
        response = requests_table.scan(
            FilterExpression='#status = :pending',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':pending': 'Pending'}
        )
        
        pending_requests = response.get('Items', [])
        print(f'Found {len(pending_requests)} pending requests')
        
        reminders_sent = 0
        
        for request in pending_requests:
            request_id = request['request_id']
            reminder_count = request.get('reminder_count', 0)
            next_reminder_at = request.get('next_reminder_at')
            
            print(f'Processing request {request_id}: reminder_count={reminder_count}, next_reminder_at={next_reminder_at}')
            
            # Skip if max reminders reached
            if reminder_count >= 3:
                print(f'Skipping {request_id}: max reminders reached')
                continue

            # Backfill next_reminder_at for old requests that don't have it set
            if not next_reminder_at:
                next_reminder_at = current_time.isoformat()
                requests_table.update_item(
                    Key={'request_id': request_id},
                    UpdateExpression='SET next_reminder_at = :t',
                    ExpressionAttributeValues={':t': next_reminder_at}
                )
                print(f'Backfilled next_reminder_at for {request_id}')
            
            # Check if it's time to send reminder
            # Parse stored IST timestamp; if no tzinfo, assume IST
            reminder_time = datetime.fromisoformat(next_reminder_at)
            if reminder_time.tzinfo is None:
                reminder_time = reminder_time.replace(tzinfo=IST)
            if current_time >= reminder_time:
                print(f'Sending reminder {reminder_count + 1} for {request_id}')
                
                # Send reminder email
                send_wfh_notification_email(request, f'reminder_{reminder_count + 1}')
                
                # Update reminder tracking
                new_reminder_count = int(reminder_count) + 1
                
                if new_reminder_count < 3:
                    # Schedule next reminder
                    next_reminder = (current_time + timedelta(hours=12)).isoformat()
                    requests_table.update_item(
                        Key={'request_id': request_id},
                        UpdateExpression='SET reminder_count = :count, last_reminder_sent = :last, next_reminder_at = :next',
                        ExpressionAttributeValues={
                            ':count': new_reminder_count,
                            ':last': current_time.isoformat(),
                            ':next': next_reminder
                        }
                    )
                    print(f'Next reminder scheduled for: {next_reminder}')
                else:
                    # Max reminders reached — remove next_reminder_at
                    requests_table.update_item(
                        Key={'request_id': request_id},
                        UpdateExpression='SET reminder_count = :count, last_reminder_sent = :last REMOVE next_reminder_at',
                        ExpressionAttributeValues={
                            ':count': new_reminder_count,
                            ':last': current_time.isoformat()
                        }
                    )
                    print(f'Max reminders reached for {request_id}')
                
                reminders_sent += 1
            else:
                print(f'Not time yet for {request_id}: reminder due at {reminder_time.isoformat()}')
        
        print(f'Reminder check completed. Reminders sent: {reminders_sent}')
        return {
            'statusCode': 200,
            'body': json.dumps({'reminders_sent': reminders_sent, 'pending_requests': len(pending_requests)})
        }
        
    except Exception as e:
        print(f'Error sending reminders: {str(e)}')
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
