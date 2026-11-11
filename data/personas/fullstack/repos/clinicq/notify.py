from twilio.rest import Client

client = Client(SID, TOKEN)


def remind(phone: str, body: str):
    client.messages.create(to=phone, from_=SENDER, body=body)
