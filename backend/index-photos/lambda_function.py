import json
import boto3
import os
import urllib.parse          # FIX 1: needed for unquote_plus and quote
import urllib.request
import urllib.error
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

ES_HOST = os.environ["ES_HOST"]
ES_INDEX = os.environ.get("ES_INDEX", "photos")
REGION = os.environ["AWS_REGION"]          # Lambda sets this automatically
session = boto3.Session()
s3 = boto3.client('s3')
rekognition = boto3.client('rekognition')


def es_request(method, path, body=None):
    url = f"https://{ES_HOST}{path}"
    data = json.dumps(body) if body is not None else None

    # 1. build + sign the request
    req = AWSRequest(method=method, url=url, data=data,
                     headers={"Content-Type": "application/json"})
    SigV4Auth(session.get_credentials(), "es", REGION).add_auth(req)

    # 2. send it with the signed headers
    http_req = urllib.request.Request(url,
                                      data=data.encode() if data else None,
                                      headers=dict(req.headers),
                                      method=method)
    try:
        with urllib.request.urlopen(http_req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print("Search domain error:", e.code, e.read().decode())
        raise


def lambda_handler(event, context):
    for record in event['Records']:
        bucket = record['s3']['bucket']['name']
        # FIX 2: decode the filename (S3 sends "my photo.jpg" as "my+photo.jpg")
        key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        print('Bucket: %s, Key: %s' % (bucket, key))

        # FIX 3: everything below is now indented INSIDE the for loop

        # --- custom labels from the file's metadata ---
        head = s3.head_object(Bucket=bucket, Key=key)
        print("Metadata:", head['Metadata'])

        labels = []
        raw = head['Metadata'].get('customlabels', '')
        for label in raw.split(','):
            label = label.strip().lower()
            if label:
                labels.append(label)
        print("Custom labels:", labels)

        # --- Rekognition labels ---
        response = rekognition.detect_labels(
            Image={"S3Object": {"Bucket": bucket, "Name": key}},
            MaxLabels=20,
            MinConfidence=70,
        )
        for item in response['Labels']:
            name = item['Name'].lower()
            if name not in labels:
                labels.append(name)
        print("All labels:", labels)

        # --- save to the search index ---
        doc = {
            "objectKey": key,
            "bucket": bucket,
            "createdTimestamp": head["LastModified"].strftime("%Y-%m-%dT%H:%M:%S"),
            "labels": labels,
        }
        doc_id = urllib.parse.quote(key, safe="")
        result = es_request("PUT", f"/{ES_INDEX}/_doc/{doc_id}", doc)
        print("Saved:", result)

    return {"statusCode": 200}     # outside the loop: runs once at the end