from googleapiclient.discovery import build

svc = build("calendar", "v3", credentials=creds)
busy = svc.freebusy().query(body={"timeMin": start, "timeMax": end, "items": [{"id": cal}]}).execute()
