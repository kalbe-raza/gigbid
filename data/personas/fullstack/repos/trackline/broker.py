import pika

conn = pika.BlockingConnection(pika.ConnectionParameters("rabbitmq"))
ch = conn.channel()
ch.queue_declare(queue="tracking-events", durable=True)
