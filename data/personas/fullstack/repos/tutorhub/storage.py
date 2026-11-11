import boto3

s3 = boto3.client("s3")
s3.upload_file(local_path, "lesson-videos", key, ExtraArgs={"ContentType": "video/mp4"})
