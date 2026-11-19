# Assignment 3 – AI Photo Search: Step-by-Step Guide

```
photo-album/
├── backend/                     ← backend GitHub repo (pipeline P1)
│   ├── index-photos/lambda_function.py    LF1
│   ├── search-photos/lambda_function.py   LF2
│   ├── create_index.py                    one-time index setup
│   └── buildspec.yml                      CodeBuild spec for P1
├── frontend/                    ← frontend GitHub repo (pipeline P2)
│   ├── index.html  app.js  style.css  config.js
│   └── apiGateway-js-sdk/      (you add this in Step 6 – the generated SDK1)
└── cloudformation/template.yaml           T1
```

How it fits together:

```
Upload:  Browser ──PUT /photos/{key}──▶ API Gateway (S3 proxy) ──▶ S3 B2 ──PUT event──▶ LF1
                                                                        LF1 ──▶ Rekognition (labels)
                                                                        LF1 ──▶ headObject (customLabels)
                                                                        LF1 ──▶ OpenSearch "photos"
Search:  Browser ──GET /search?q=──▶ API Gateway ──▶ LF2 ──▶ Lex (keywords) ──▶ OpenSearch ──▶ results
Site:    Browser ──▶ S3 B1 (static website)
```

Use **one region for everything** (e.g. `us-east-1`). Note: "ElasticSearch Service" is now called **Amazon OpenSearch Service**, and Lex V1 is retired, so this guide uses **Lex V2**. Both work the same way for this assignment.

---

## Step 1: Launch the search domain ("photos")

1. Console → **Amazon OpenSearch Service** → **Create domain**.
2. Name: `photos`. Choose **Standard create**, template **Dev/test**, deployment **without standby**, **1 AZ**, instance `t3.small.search`, 1 node, 10 GB EBS. Don't choose Serverless.
3. Network: **Public access**.
4. **Uncheck "Enable fine-grained access control"** (this is the simplest setup: access is then controlled by IAM only).
5. Access policy → **Configure domain level access policy** → JSON:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "AWS": "arn:aws:iam::<ACCOUNT_ID>:root" },
       "Action": "es:*",
       "Resource": "arn:aws:es:<REGION>:<ACCOUNT_ID>:domain/photos/*"
     }]
   }
   ```
   This lets any IAM role in your account that has `es:` permissions (your Lambdas) reach the domain.
6. Create. It takes about 15 minutes. Copy the **Domain endpoint** *without* `https://`, e.g. `search-photos-abc123.us-east-1.es.amazonaws.com`. This is `ES_HOST`.
7. **Create the index:** open **CloudShell** (top bar icon), upload `backend/create_index.py`, run:
   ```bash
   python3 create_index.py <ES_HOST> us-east-1
   ```
   You should see `{"acknowledged":true,...}`. This sets an English analyzer on `labels`, so a search for "dogs" matches the label "dog".

> ⚠️ OpenSearch bills hourly. Delete the domain after grading.

---

## Step 2: Build the Lex bot (SearchIntent)

1. Console → **Amazon Lex V2** → **Create bot** → *Create a blank bot*.
   Name `PhotoSearchBot`, IAM: *Create a role with basic Lex permissions*, COPPA: No. Language **English (US)**.
2. **Slot type** (left menu → Slot types → Add blank slot type): name `PhotoKeyword`.
   - Slot value resolution: **Expand values**. This is the key setting, because it lets Lex accept words you didn't list.
   - Add ~15 sample values: `dog, cat, tree, bird, car, person, beach, mountain, flower, food, building, sky, water, city, animal`.
3. **Intent** → rename/create `SearchIntent`.
   - Slots: `keywordOne` (type `PhotoKeyword`, **Required**, prompt "What would you like to search for?") and `keywordTwo` (type `PhotoKeyword`, **optional**).
   - Sample utterances (add all):
     ```
     {keywordOne}
     {keywordOne} and {keywordTwo}
     {keywordOne} {keywordTwo}
     show me {keywordOne}
     show me {keywordOne} and {keywordTwo}
     show me photos with {keywordOne}
     show me photos with {keywordOne} in them
     show me photos with {keywordOne} and {keywordTwo}
     show me photos with {keywordOne} and {keywordTwo} in them
     show me pictures of {keywordOne}
     show me pictures of {keywordOne} and {keywordTwo}
     find {keywordOne}
     find {keywordOne} and {keywordTwo}
     photos of {keywordOne}
     photos of {keywordOne} and {keywordTwo}
     ```
   - Fulfillment: leave as is (no code hook needed; LF2 reads the slots directly).
4. **Save intent → Build**. Click **Test** and try "show me trees and birds". The inspect panel should show both slots filled.
5. Copy the **Bot ID** (bot overview page). For the alias you can use the built-in test alias **`TSTALIASID`**. (Optional: create a Version + Alias and use that alias ID instead.)

---

## Step 3: Package the Lambda code

In CloudShell (or locally):

```bash
cd backend
(cd index-photos  && zip -r ../index-photos.zip .)
(cd search-photos && zip -r ../search-photos.zip .)
aws s3 mb s3://<your-name>-lambda-code
aws s3 cp index-photos.zip  s3://<your-name>-lambda-code/
aws s3 cp search-photos.zip s3://<your-name>-lambda-code/
```

No extra libraries are needed. Both Lambdas sign OpenSearch requests with `botocore`, which is already in the Lambda runtime.

---

## Step 4: Deploy the stack with CloudFormation (T1)

This single template creates **B1, B2, LF1, LF2, the S3→LF1 PUT trigger, the API (both methods), the API key + usage plan, the deployment, and all IAM roles/permissions**. That covers components 2a–c, 3a, 4a–d, 5b–c and 7.

```bash
aws cloudformation deploy \
  --template-file cloudformation/template.yaml \
  --stack-name photo-search \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      PhotosBucketName=<your-name>-photos-b2 \
      FrontendBucketName=<your-name>-photos-frontend-b1 \
      LambdaCodeBucket=<your-name>-lambda-code \
      EsHost=<ES_HOST> \
      LexBotId=<BOT_ID> \
      LexBotAliasId=TSTALIASID

aws cloudformation describe-stacks --stack-name photo-search --query "Stacks[0].Outputs"
```

From the outputs, note **FrontendWebsiteURL**, **ApiInvokeURL** and **ApiKeyId**, then get the key value:

```bash
aws apigateway get-api-key --api-key <ApiKeyId> --include-value --query value --output text
```

**What the API looks like (for your write-up):**
- `GET /search?q=` → Lambda proxy integration to LF2, API key required.
- `PUT /photos/{object}` → **AWS service integration, S3, PUT, path override `<B2>/{object}`**, using a role with `s3:PutObject`. Method request headers `Content-Type` and `x-amz-meta-customLabels` are mapped to the integration request headers, so the custom labels land on the S3 object as metadata. Binary media types `image/png`, `image/jpeg` are enabled so images pass through intact.
- OPTIONS methods + gateway responses provide CORS.

(The Swagger file names the upload path `/upload`, but the assignment text says `PUT /photos`, so this follows the assignment.)

**Test the indexing path now (component 2c-i):**
```bash
API=<ApiInvokeURL>; KEY=<api key value>
curl -X PUT "$API/photos/test-dog.jpg" -H "x-api-key: $KEY" \
     -H "Content-Type: image/jpeg" -H "x-amz-meta-customLabels: Sam, Sally" \
     --data-binary @some-dog-photo.jpg
```
Then CloudWatch → Log groups → `/aws/lambda/index-photos`. You should see the custom labels, the Rekognition labels and `Indexed: {... "result":"created"}`.

**Test search:**
```bash
curl "$API/search?q=show%20me%20dogs" -H "x-api-key: $KEY"
curl "$API/search?q=sam" -H "x-api-key: $KEY"
```

---

## Step 5: How the two Lambdas work (for your report)

**LF1 `index-photos`** (`backend/index-photos/lambda_function.py`)
1. Reads bucket + key from the S3 PUT event (E1).
2. `s3.head_object` → `Metadata["customlabels"]` (boto3 lower-cases metadata keys) → splits on commas → JSON array **A1**.
3. `rekognition.detect_labels` (MaxLabels 20, MinConfidence 70) → appends each label (lower-cased, de-duplicated) to A1.
4. PUTs `{objectKey, bucket, createdTimestamp, labels}` into the `photos` index, using the object key as the document id.

**LF2 `search-photos`** (`backend/search-photos/lambda_function.py`)
1. Reads `q` from the query string.
2. `lexv2-runtime.recognize_text` → if the intent is `SearchIntent`, collects `keywordOne`/`keywordTwo` (K1…Kn).
3. No keywords → `{"results": []}`.
4. Otherwise runs a `bool/should` query (a photo matches if **any** keyword matches **any** label), so "show me cats and dogs" returns cat photos *and* dog photos.
5. Returns `{"results":[{"url": <presigned S3 URL>, "labels":[...]}]}` per the Swagger `SearchResponse`. Presigned URLs mean B2 can stay private.

---

## Step 6: Frontend + generated SDK (SDK1)

1. Console → API Gateway → `ai-photo-search` → **Stages** → `v1` → **Stage actions → Generate SDK** → Platform **JavaScript** → download.
2. Unzip it into `frontend/` so you have `frontend/apiGateway-js-sdk/apigClient.js` etc.
3. Edit `frontend/config.js`: set `apiKey` and `invokeUrl` (no trailing slash).
4. Upload:
   ```bash
   aws s3 sync frontend/ s3://<your-name>-photos-frontend-b1/
   ```
5. Open **FrontendWebsiteURL**. Upload a photo with labels "Sam, Sally", wait about 5 seconds, then search "show me Sam" and "dogs".

Search uses the generated SDK (`apigClient.searchGet`), which is the SDK integration requirement. Upload uses `fetch` against the same API with the same API key, because it sends the raw image bytes reliably. The labels box is turned into `x-amz-meta-customLabels: Sam, Sally`, exactly as the assignment specifies.

---

## Step 7: CodePipeline (P1 backend, P2 frontend)

Push `backend/` and `frontend/` to **two GitHub repos**.

**P1 – backend**
1. CodePipeline → Create pipeline → *Build custom pipeline*. Name `photo-backend`.
2. Source: **GitHub (via GitHub App)** → connect → backend repo, branch `main`.
3. Build: **AWS CodeBuild** → *Create project*: managed image Amazon Linux, standard runtime, **use a buildspec file** (`buildspec.yml` at the repo root).
4. **Skip deploy stage.** The buildspec itself runs `aws lambda update-function-code`.
5. After creating it, go to IAM → the CodeBuild service role → add an inline policy:
   ```json
   {"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"lambda:UpdateFunctionCode",
     "Resource":["arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:index-photos",
                 "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:search-photos"]}]}
   ```
6. Release change → check that the Lambda "Last modified" time updates.

**P2 – frontend**
1. New pipeline `photo-frontend`, source = frontend repo.
2. **Skip build** (static files need no build).
3. Deploy: provider **Amazon S3**, bucket = B1, **check "Extract file before deploy"**.
4. Commit a small change (e.g. the page title), push, and confirm the website updates.

(Remember to commit the `apiGateway-js-sdk/` folder and a filled-in `config.js` to the frontend repo. The API key ends up public in the browser anyway; it's for metering, not secrecy.)

---

## Step 8: Acceptance-criteria checklist

| # | Check | How |
|---|-------|-----|
| 1 | T1 creates resources | Delete and redeploy the stack with a new name; both Lambdas, API, B1, B2 come up; the website URL is in Outputs |
| 2 | Push → deploy | Commit to each repo and watch P1/P2 run green |
| 3 | customLabels | Upload with "Sam, Sally"; S3 console → object → Metadata shows `x-amz-meta-customlabels`; search "Sam" returns it |
| 4 | Every Rekognition label is searchable | Copy the labels from the LF1 log, search a few one at a time ("show me tree") and in pairs ("show me cars and people") |
| 5 | Everything else | Empty or nonsense query → `{"results": []}`, no crash |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| LF1 never runs | B2 → Properties → Event notifications should list a PUT → `index-photos`. Uploads through the console use PUT; multipart (>~16 MB via CLI) uses a different event |
| `403` from OpenSearch in logs | Domain access policy (Step 1.5) or fine-grained access control left on. If FGAC is on, map the Lambda role ARNs to `all_access` in OpenSearch Dashboards → Security → Roles |
| `{"message":"Forbidden"}` from API | Missing/wrong `x-api-key`, or the usage plan isn't attached to stage `v1` |
| CORS error in browser | Look at the real status code in DevTools → Network. Usually it's a 403/500 underneath. Redeploy the API after any console change |
| Uploaded image is corrupt | Binary media types missing, or `Content-Type` not sent as `image/jpeg`/`image/png` |
| Search returns nothing but labels exist | Test the bot in the Lex console. If slots are empty, add more utterances/sample values and **Build** again. Check that the LF2 log shows `Keywords: [...]` |
| `AccessDeniedException` from Lex | LF2 role needs `lex:RecognizeText`; check `BOT_ID`/`BOT_ALIAS_ID`/region |
| Upload fails >10 MB | API Gateway payload limit. Use smaller images |
