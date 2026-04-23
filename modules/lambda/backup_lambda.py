import json
import os
import boto3
from datetime import datetime
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Lambda function to backup DynamoDB tables to S3 with versioning
    File naming: table_name-YYYY-MM-DD-WFH.json
    """
    
    dynamodb = boto3.resource('dynamodb')
    s3 = boto3.client('s3')
    
    # Configuration — bucket name injected via Terraform env var
    BUCKET_NAME = os.environ['BACKUP_BUCKET_NAME']
    
    # DynamoDB tables to backup (must match tables created by IaC)
    TABLES_TO_BACKUP = [
        os.environ.get('TABLE_WFH_REQUESTS', 'WFH_Requests'),
        os.environ.get('TABLE_WFH_SETTINGS', 'WFH-Settings'),
        os.environ.get('TABLE_WFH_USERS', 'wfh-users'),
    ]
    current_date = datetime.now().strftime('%Y-%m-%d')
    backup_results = []
    
    try:
        for table_name in TABLES_TO_BACKUP:
            logger.info(f"Starting backup for table: {table_name}")
            
            # Get table data
            table = dynamodb.Table(table_name)
            response = table.scan()
            items = response['Items']
            
            # Handle pagination
            while 'LastEvaluatedKey' in response:
                response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                items.extend(response['Items'])
            
            # Convert Decimal to float for JSON serialization
            items_json = json.dumps(items, default=decimal_default, indent=2)
            
            # Create S3 key with required format
            s3_key = f"{table_name}-{current_date}-WFH.json"
            
            # Upload to S3
            s3.put_object(
                Bucket=BUCKET_NAME,
                Key=s3_key,
                Body=items_json,
                ContentType='application/json',
                Metadata={
                    'backup_date': current_date,
                    'table_name': table_name,
                    'record_count': str(len(items))
                }
            )
            
            backup_results.append({
                'table': table_name,
                'records': len(items),
                's3_key': s3_key,
                'status': 'success'
            })
            
            logger.info(f"Backup completed for {table_name}: {len(items)} records")
    
    except Exception as e:
        logger.error(f"Backup failed: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'partial_results': backup_results
            })
        }
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Backup completed successfully',
            'backup_date': current_date,
            'results': backup_results
        })
    }

def decimal_default(obj):
    """JSON serializer for Decimal objects"""
    if isinstance(obj, boto3.dynamodb.types.TypeDeserializer().deserialize({'N': '1'}).__class__):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
