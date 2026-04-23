def lambda_handler(event, context):
    email = event['request']['userAttributes'].get('email', '').lower()
    if not email.endswith('@comprinno.net'):
        raise Exception('Only @comprinno.net email addresses are allowed to register')
    event['response']['autoConfirmUser'] = False
    event['response']['autoVerifyEmail'] = False
    return event
