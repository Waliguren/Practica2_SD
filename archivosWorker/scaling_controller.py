import os
import json
import boto3
from datetime import datetime, timedelta, timezone

SQS_QUEUE_URL = os.getenv('SQS_QUEUE_URL')
LAMBDA_FUNCTION_NAME = os.getenv('LAMBDA_FUNCTION_NAME', 'ticket-worker')
TARGET_RESPONSE_TIME = int(os.getenv('TARGET_RESPONSE_TIME', '5'))
MIN_CONCURRENCY = int(os.getenv('MIN_CONCURRENCY', '1'))
MAX_CONCURRENCY = int(os.getenv('MAX_CONCURRENCY', '9'))
CAPACITY_PER_WORKER = float(os.getenv('CAPACITY_PER_WORKER', '8.0'))
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')

sqs = boto3.client('sqs', region_name=AWS_REGION)
cloudwatch = boto3.client('cloudwatch', region_name=AWS_REGION)
lambda_client = boto3.client('lambda', region_name=AWS_REGION)

def get_queue_backlog():
    response = sqs.get_queue_attributes(
        QueueUrl=SQS_QUEUE_URL,
        AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible']
    )
    visible = int(response['Attributes'].get('ApproximateNumberOfMessages', '0'))
    not_visible = int(response['Attributes'].get('ApproximateNumberOfMessagesNotVisible', '0'))
    return visible + not_visible

def get_arrival_rate():
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(seconds=60)

    response = cloudwatch.get_metric_statistics(
        Namespace='AWS/SQS',
        MetricName='NumberOfMessagesReceived',
        Dimensions=[{'Name': 'QueueName', 'Value': SQS_QUEUE_URL.split('/')[-1]}],
        StartTime=start_time,
        EndTime=end_time,
        Period=60,
        Statistics=['Sum']
    )

    datapoints = response.get('Datapoints', [])
    if datapoints:
        return max(0, datapoints[-1]['Sum'] / 60.0)
    return 0

def get_worker_capacity():
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=5)

    response = cloudwatch.get_metric_statistics(
        Namespace='AWS/Lambda',
        MetricName='Duration',
        Dimensions=[
            {'Name': 'FunctionName', 'Value': LAMBDA_FUNCTION_NAME},
            {'Name': 'Resource', 'Value': f'{LAMBDA_FUNCTION_NAME}:prod'}
        ],
        StartTime=start_time,
        EndTime=end_time,
        Period=300,
        Statistics=['Average']
    )

    datapoints = response.get('Datapoints', [])
    if datapoints:
        avg_duration_ms = datapoints[-1]['Average']
        return max(1, 1000.0 / max(avg_duration_ms, 1))
    return CAPACITY_PER_WORKER

def lambda_handler(event, context):
    try:
        backlog = get_queue_backlog()
        arrival_rate = get_arrival_rate()
        capacity = get_worker_capacity()

        desired = ((backlog / TARGET_RESPONSE_TIME) + arrival_rate) / capacity
        desired = max(MIN_CONCURRENCY, min(MAX_CONCURRENCY, int(desired + 0.5)))

        current_config = lambda_client.get_function_configuration(
            FunctionName=LAMBDA_FUNCTION_NAME
        )
        current_concurrency = current_config.get('ReservedConcurrentExecutions', 0)

        if desired != current_concurrency:
            lambda_client.put_function_concurrency(
                FunctionName=LAMBDA_FUNCTION_NAME,
                ReservedConcurrentExecutions=int(desired)
            )
            print(f"Scaled: {current_concurrency} -> {desired} (backlog={backlog}, "
                  f"arrival={arrival_rate:.1f}/s, capacity={capacity:.1f}/s)")

        cloudwatch.put_metric_data(
            Namespace='TicketSystem',
            MetricData=[
                {'MetricName': 'DesiredConcurrency', 'Value': float(desired), 'Unit': 'Count'},
                {'MetricName': 'CurrentConcurrency', 'Value': float(current_concurrency), 'Unit': 'Count'},
                {'MetricName': 'QueueBacklog', 'Value': float(backlog), 'Unit': 'Count'},
                {'MetricName': 'ArrivalRate', 'Value': float(arrival_rate), 'Unit': 'Count/Second'},
                {'MetricName': 'WorkerCapacity', 'Value': float(capacity), 'Unit': 'Count/Second'},
            ]
        )

        return {
            'statusCode': 200,
            'body': json.dumps({
                'desired_concurrency': desired,
                'current_concurrency': current_concurrency,
                'backlog': backlog,
                'arrival_rate': arrival_rate,
                'worker_capacity': capacity
            })
        }

    except Exception as e:
        print(f"Scaling controller error: {e}")
        return {'statusCode': 500, 'body': str(e)}