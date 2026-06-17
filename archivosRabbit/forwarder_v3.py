import os, json, time, boto3, pika

HOST = os.getenv('RABBITMQ_HOST', 'localhost')
SQS_URL = os.getenv('SQS_QUEUE_URL', 'https://sqs.us-east-1.amazonaws.com/344602101473/ticket-queue')
BATCH = 10
POLL = 0.02

sqs = boto3.client('sqs', region_name='us-east-1')

def main():
    creds = pika.PlainCredentials('admin', 'admin123')
    params = pika.ConnectionParameters(host=HOST, port=5672, credentials=creds, heartbeat=600)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.queue_declare(queue='booking_queue', durable=True)
    total = 0
    print(f'Forwarder started. SQS: {SQS_URL}')

    while True:
        msgs = []
        tags = []
        for _ in range(BATCH):
            mf, props, body = ch.basic_get(queue='booking_queue', auto_ack=False)
            if mf is None:
                break
            msgs.append({'Id': str(len(msgs)), 'MessageBody': body.decode('utf-8')})
            tags.append(mf.delivery_tag)
        if not msgs:
            time.sleep(POLL)
            continue
        success = 0
        try:
            resp = sqs.send_message_batch(QueueUrl=SQS_URL, Entries=msgs)
            ok_ids = {s['Id'] for s in resp.get('Successful', [])}
            for i, dt in enumerate(tags):
                if str(i) in ok_ids:
                    ch.basic_ack(delivery_tag=dt)
                    success += 1
                else:
                    ch.basic_nack(delivery_tag=dt, requeue=True)
        except Exception as e:
            print(f'SQS error: {e}')
            for dt in tags:
                ch.basic_nack(delivery_tag=dt, requeue=True)
        total += success
        if success != BATCH:
            time.sleep(POLL)
        if total > 0 and total % 1000 == 0:
            print(f'[forwarder] {total} forwarded')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
