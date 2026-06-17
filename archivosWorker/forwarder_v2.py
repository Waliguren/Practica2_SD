import os, json, time, boto3, pika

RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
SQS_QUEUE_URL = os.getenv('SQS_QUEUE_URL', 'https://sqs.us-east-1.amazonaws.com/344602101473/ticket-queue')
BATCH_SIZE = 10
BATCH_TIMEOUT = 0.5

sqs = boto3.client('sqs', region_name='us-east-1')

class Forwarder:
    def __init__(self):
        self.batch = []
        self.last_flush = time.time()
        self.total = 0

        creds = pika.PlainCredentials('admin', 'admin123')
        params = pika.ConnectionParameters(host=RABBITMQ_HOST, port=5672, credentials=creds, heartbeat=600)
        self.conn = pika.BlockingConnection(params)
        self.ch = self.conn.channel()
        self.ch.queue_declare(queue='booking_queue', durable=True)
        self.ch.basic_qos(prefetch_count=BATCH_SIZE * 2)

    def flush(self):
        if not self.batch:
            return
        entries = [{'Id': str(i), 'MessageBody': b} for i, (_, b) in enumerate(self.batch)]
        delivery_tags = [dt for dt, _ in self.batch]
        try:
            resp = sqs.send_message_batch(QueueUrl=SQS_QUEUE_URL, Entries=entries)
            ok = {s['Id'] for s in resp.get('Successful', [])}
            for i, dt in enumerate(delivery_tags):
                if str(i) in ok:
                    self.ch.basic_ack(delivery_tag=dt)
                    self.total += 1
                else:
                    self.ch.basic_nack(delivery_tag=dt, requeue=True)
        except Exception as e:
            print(f'SQS error: {e}')
            for dt in delivery_tags:
                self.ch.basic_nack(delivery_tag=dt, requeue=True)
        self.batch = []
        self.last_flush = time.time()

    def on_message(self, ch, method, props, body):
        self.batch.append((method.delivery_tag, body.decode('utf-8')))
        if len(self.batch) >= BATCH_SIZE:
            self.flush()

    def run(self):
        print(f'Forwarder started. SQS queue: {SQS_QUEUE_URL}')
        self.ch.basic_consume(queue='booking_queue', on_message_callback=self.on_message)
        while True:
            self.conn.process_data_events(time_limit=0.05)
            if self.batch and (time.time() - self.last_flush) >= BATCH_TIMEOUT:
                self.flush()
            if self.total > 0 and self.total % 500 == 0:
                print(f'[forwarder] {self.total} forwarded')

if __name__ == '__main__':
    f = Forwarder()
    try:
        f.run()
    except KeyboardInterrupt:
        f.flush()
        f.conn.close()
