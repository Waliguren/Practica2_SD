import os
import json
import time
import boto3
import pika

RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
SQS_QUEUE_URL = os.getenv('SQS_QUEUE_URL')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '10'))
POLL_INTERVAL = float(os.getenv('POLL_INTERVAL', '0.05'))

sqs = boto3.client('sqs', region_name=AWS_REGION)

def forward_batch(channel):
    messages = []
    deliveries = []

    for _ in range(BATCH_SIZE):
        method_frame, properties, body = channel.basic_get(queue='booking_queue', auto_ack=False)
        if method_frame is None:
            break
        messages.append({
            'Id': str(len(messages)),
            'MessageBody': body.decode('utf-8')
        })
        deliveries.append((method_frame.delivery_tag, body))

    if not messages:
        return 0

    try:
        response = sqs.send_message_batch(
            QueueUrl=SQS_QUEUE_URL,
            Entries=messages
        )

        successful_ids = {s['Id'] for s in response.get('Successful', [])}
        for i, (delivery_tag, body) in enumerate(deliveries):
            if str(i) in successful_ids:
                channel.basic_ack(delivery_tag=delivery_tag)
            else:
                channel.basic_nack(delivery_tag=delivery_tag, requeue=True)

        return len(successful_ids)
    except Exception as e:
        print(f"Error sending batch to SQS: {e}")
        for delivery_tag, _ in deliveries:
            channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
        return 0

def main():
    print(f"Connecting to RabbitMQ at {RABBITMQ_HOST}...")
    credentials = pika.PlainCredentials('admin', 'admin123')
    parameters = pika.ConnectionParameters(
        host=RABBITMQ_HOST, port=5672, credentials=credentials,
        heartbeat=600, blocked_connection_timeout=300
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue='booking_queue', durable=True)

    print(f"Forwarder started. Sending to SQS queue: {SQS_QUEUE_URL}")
    total_forwarded = 0

    while True:
        try:
            count = forward_batch(channel)
            total_forwarded += count
            if count == 0:
                time.sleep(POLL_INTERVAL)
            else:
                print(f"Forwarded {count} messages (total: {total_forwarded})")
        except Exception as e:
            print(f"Error in forward loop: {e}")
            time.sleep(1)

if __name__ == '__main__':
    main()
