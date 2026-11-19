import json
import boto3
import os
import uuid
import urllib.request
import urllib.error
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

lex = boto3.client('lexv2-runtime')
s3 = boto3.client('s3')
BOT_ALIAS_ID = os.getenv('BOT_ALIAS_ID', 'TSTALIASID')   # FIX 1: defaults if unset
BOT_ID       = os.getenv('BOT_ID')
LOCALE_ID    = os.getenv('LOCALE_ID', 'en_US')
ES_HOST  = os.environ["ES_HOST"]
ES_INDEX = os.environ.get("ES_INDEX", "photos")
REGION   = os.environ.get("AWS_REGION", "us-east-1")
session  = boto3.Session()

# FIX 2: defined at the top, so BOTH the success and error responses can use it
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,X-Api-Key",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
    "Content-Type": "application/json",
}


def respond(status, body):
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(body)}


def es_request(method, path, body=None):
    url = f"https://{ES_HOST}{path}"
    data = json.dumps(body) if body is not None else None
    req = AWSRequest(method=method, url=url, data=data,
                     headers={"Content-Type": "application/json"})
    SigV4Auth(session.get_credentials(), "es", REGION).add_auth(req)
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


def get_keywords(query):
    response = lex.recognize_text(
        botId=BOT_ID,
        botAliasId=BOT_ALIAS_ID,
        localeId=LOCALE_ID,
        sessionId=str(uuid.uuid4()),
        text=query,
    )
    intent = response.get("sessionState", {}).get("intent", {})
    # FIX 3: only accept our search intent (e.g. "hello" -> FallbackIntent -> no keywords)
    if intent.get("name") != "SearchIntent":
        return []
    keywords = []
    slots = intent.get("slots") or {}
    for slot in slots.values():
        if slot and slot.get("value") and slot["value"].get("interpretedValue"):
            keywords.append(slot["value"]["interpretedValue"].strip().lower())
    return keywords


def search_index(keywords):
    should = [{"match": {"labels": kw}} for kw in keywords]
    query = {
        "size": 100,
        "query": {"bool": {"should": should, "minimum_should_match": 1}},
    }
    response = es_request("POST", f"/{ES_INDEX}/_search", query)
    hits = response["hits"]["hits"]
    print("Found", len(hits), "photos")
    return [hit["_source"] for hit in hits]


def get_photo_url(photos):
    results = []
    for photo in photos:
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': photo['bucket'], 'Key': photo['objectKey']},
            ExpiresIn=3600,
        )
        results.append({"url": url, "labels": photo["labels"]})
    print("Returning", len(results), "results")
    return results


def lambda_handler(event, context):
    try:
        print("EVENT:", json.dumps(event))

        # FIX 4: no crash when queryStringParameters is None or q is missing
        query = (event.get("queryStringParameters") or {}).get("q", "").strip()
        print("QUERY:", query)
        if not query:
            return respond(200, {"results": []})

        keywords = get_keywords(query)
        print("Keywords:", keywords)

        # FIX 5: assignment says: no keywords -> empty results (don't search)
        if not keywords:
            return respond(200, {"results": []})

        photos = search_index(keywords)
        results = get_photo_url(photos)
        return respond(200, {"results": results})

    except Exception as e:                       # FIX 6: "as e" so e exists
        print("Error:", repr(e))
        # FIX 7: error format matches the API spec (code + message)
        return respond(500, {"code": 500, "message": str(e)})